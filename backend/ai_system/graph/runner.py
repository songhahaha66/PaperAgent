from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from .checkpoint import save_checkpoint
from .context import RunContext, bind_emitter, emit, emit_json
from .nodes.answer import answer
from .nodes.commit import commit_drafts
from .nodes.context import load_context
from .nodes.draft import DraftError, draft_slot
from .nodes.finalize import finalize
from .nodes.gather import gather_batch
from .nodes.intent import classify_intent
from .nodes.judge_node import judge_draft
from .nodes.plan import persist_plan, plan_node
from .nodes.render_node import render_node
from .nodes.spec import ensure_template_spec
from .nodes.triage import triage_issues
from .nodes.validate_node import validate_node
from .state import PaperState
from ..schemas.draft import Judgement

logger = logging.getLogger(__name__)


async def run_pipeline(
    *,
    work_id: str,
    workspace_dir: str,
    user_message: str,
    template_id: int | None = None,
    output_mode: str = "markdown",
    run_id: str | None = None,
    llm=None,
    writer_llm=None,
    stream_manager=None,
    writer=None,
    gather_fn=None,
    judge=None,
    coder_llm=None,
    max_repair_rounds: int = 2,
) -> str:
    run_id = run_id or uuid.uuid4().hex[:12]
    emitter = bind_emitter(stream_manager, workspace_dir, run_id, work_id)
    ctx = RunContext(
        llm=llm,
        writer_llm=writer_llm,
        stream=stream_manager,
        emitter=emitter,
        writer=writer,
        gather_fn=gather_fn,
        judge=judge,
        coder_llm=coder_llm,
    )
    state = PaperState(
        work_id=work_id,
        run_id=run_id,
        workspace_dir=workspace_dir,
        template_id=template_id,
        output_mode=output_mode,
        user_message=user_message,
        max_repair_rounds=max_repair_rounds,
    )
    await emit(ctx, "RUN_STARTED", {"work_id": work_id}, run_id=run_id, thread_id=work_id)
    await emit_json(ctx, "main_agent_start", f"开始执行: {user_message[:80]}")

    try:
        state = await _run_graph(state, ctx)
        await emit(ctx, "RUN_FINISHED", {"summary": state.summary}, run_id=run_id, thread_id=work_id)
        # A streamed answer already reached the chat; avoid echoing the same text as a card.
        await emit_json(ctx, "main_agent_complete", "已回答。" if state.streamed_answer else state.summary)
        return state.summary
    except Exception as exc:
        await emit(ctx, "RUN_ERROR", {"error": str(exc)}, run_id=run_id, thread_id=work_id)
        await emit_json(ctx, "main_agent_error", str(exc))
        raise


async def _step(ctx: RunContext, state: PaperState, node: str, **extra) -> None:
    await emit(ctx, "STEP_STARTED", {"node": node, **extra}, run_id=state.run_id, thread_id=state.work_id)


async def _classify(state: PaperState, ctx: RunContext) -> PaperState:
    # Judges may call blocking HTTP/LLM clients; keep them off the event loop.
    return await asyncio.to_thread(classify_intent, state, ctx.judge)


async def _run_graph(state: PaperState, ctx: RunContext) -> PaperState:
    await _step(ctx, state, "load_context")
    state = load_context(state)
    await _step(ctx, state, "classify_intent")
    state = await _classify(state, ctx)
    if state.intent.kind in {"question", "chat"}:
        state = await answer(state, llm=ctx.llm, ctx=ctx)
        return finalize(state)
    if state.intent.kind == "confirm":
        state.awaiting_confirmation = False
        state.pending_confirmation = False
        state.summary = "已确认采用当前稿，停止自动修复。"
        await emit(ctx, "CUSTOM", {"type": "confirmation_resolved"}, run_id=state.run_id, thread_id=state.work_id)
        if state.rendered_path:
            await _emit_render_done(ctx, state)
        state = finalize(state)
        save_checkpoint(state)
        return state

    await _step(ctx, state, "ensure_template_spec")
    state = await asyncio.to_thread(ensure_template_spec, state, ctx.judge)
    if state.intent.kind == "edit" and not state.intent.target_slots:
        state = await _classify(state, ctx)

    state.failed_slots = {}
    while True:
        state = plan_node(state)
        projected = await persist_plan(state, lambda block, content: emit_json(ctx, block, content))
        await emit(
            ctx,
            "STATE_DELTA",
            {"type": "plan_updated", "content": projected, "path": "/plan"},
            run_id=state.run_id,
            thread_id=state.work_id,
        )
        save_checkpoint(state)

        for batch in state.plan.batches if state.plan else []:
            await _step(ctx, state, "gather", slots=batch)
            state = await gather_batch(
                state,
                batch,
                gather_fn=ctx.gather_fn,
                coder_llm=ctx.coder_llm,
                stream=ctx.stream,
            )
            slot_map = {slot.id: slot for slot in (state.spec.slots if state.spec else [])}
            drafted = await asyncio.gather(
                *[_draft_and_judge(slot_map[slot_id], state, ctx) for slot_id in batch if slot_id in slot_map]
            )
            passed = [draft for draft, judgement in drafted if draft is not None and judgement.passed]
            if passed:
                state = commit_drafts(state, passed)
            save_checkpoint(state)

        await _step(ctx, state, "render")
        state = await asyncio.to_thread(render_node, state)
        await _emit_render_done(ctx, state)
        if state.rendered_path:
            await emit_json(ctx, "file_changed", Path(state.rendered_path).name)

        # Validation may shell out to soffice; keep the event loop responsive.
        await _step(ctx, state, "validate")
        state = await asyncio.to_thread(validate_node, state)
        for issue in state.issues:
            await emit(
                ctx,
                "CUSTOM",
                {"type": "validation_issue", **issue.model_dump()},
                run_id=state.run_id,
                thread_id=state.work_id,
            )

        if state.issues:
            rounds_before = state.repair_round
            state = triage_issues(state)
            if state.repair_round > rounds_before:
                continue
            if state.awaiting_confirmation:
                await emit(
                    ctx,
                    "CUSTOM",
                    {"type": "awaiting_confirmation", "issues": [issue.model_dump() for issue in state.issues]},
                    run_id=state.run_id,
                    thread_id=state.work_id,
                )
        break

    # Re-derive the plan from the IR so the projection reflects what was actually committed.
    state.intent.target_slots = []
    state = plan_node(state)
    await persist_plan(state, lambda block, content: emit_json(ctx, block, content))
    state = finalize(state)
    save_checkpoint(state)
    if state.summary and ctx.stream and hasattr(ctx.stream, "print_main_content"):
        await ctx.stream.print_main_content(state.summary)
    return state


async def _emit_render_done(ctx: RunContext, state: PaperState) -> None:
    await emit(
        ctx,
        "CUSTOM",
        {
            "type": "render_done",
            "format": state.output_mode,
            "path": state.rendered_path,
            "revision": state.ir.revision if state.ir else 0,
        },
        run_id=state.run_id,
        thread_id=state.work_id,
    )


def _slot_feedback(state: PaperState, slot_id: str, judgement: Judgement | None = None) -> str:
    parts = [issue.detail or issue.code for issue in state.issues if issue.slot_id == slot_id]
    if judgement is not None and not judgement.passed:
        if not judgement.substantive:
            parts.append("内容不够充实或只是复述标题，请写出实质性正文")
        if judgement.example_left:
            parts.append("残留了模板示例文字，必须删除或替换")
        if judgement.follows_rules == "明显违反":
            parts.append("明显违反槽位约束")
        if judgement.reason and judgement.reason not in {"judge", "内容充足"}:
            parts.append(judgement.reason)
    return "；".join(dict.fromkeys(part for part in parts if part))


async def _draft_and_judge(slot, state: PaperState, ctx: RunContext):
    await _step(ctx, state, "draft", slot_id=slot.id, slot_title=slot.title)
    feedback = _slot_feedback(state, slot.id)
    draft = None
    judgement = Judgement(passed=False, reason="未生成草稿")
    repairs = state.slot_repairs.get(slot.id, 0)
    while True:
        try:
            draft = await draft_slot(slot, state, ctx, feedback=feedback)
        except DraftError as exc:
            logger.warning("槽位 %s 起草失败: %s", slot.id, exc)
            state.failed_slots[slot.id] = str(exc)
            judgement = Judgement(passed=False, reason=str(exc))
            break
        judgement = await asyncio.to_thread(judge_draft, draft, slot, ctx.judge)
        if judgement.passed:
            state.failed_slots.pop(slot.id, None)
            break
        if repairs >= state.max_repair_rounds:
            state.failed_slots[slot.id] = judgement.reason or "评审未通过"
            break
        repairs += 1
        state.slot_repairs[slot.id] = repairs
        feedback = _slot_feedback(state, slot.id, judgement)
        await _step(ctx, state, "revise", slot_id=slot.id, slot_title=slot.title)
    await emit(
        ctx,
        "STEP_FINISHED",
        {"node": "draft", "slot_id": slot.id, "passed": judgement.passed, "reason": judgement.reason},
        run_id=state.run_id,
        thread_id=state.work_id,
    )
    return draft, judgement
