from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from .checkpoint import save_checkpoint
from .context import RunContext, bind_emitter, emit, emit_json
from .nodes.answer import answer
from .nodes.commit import commit_drafts
from .nodes.context import load_context
from .nodes.draft import draft_slot, heuristic_draft
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
        await emit_json(ctx, "main_agent_complete", state.summary)
        return state.summary
    except Exception as exc:
        await emit(ctx, "RUN_ERROR", {"error": str(exc)}, run_id=run_id, thread_id=work_id)
        await emit_json(ctx, "main_agent_error", str(exc))
        raise


async def _run_graph(state: PaperState, ctx: RunContext) -> PaperState:
    await emit(ctx, "STEP_STARTED", {"node": "load_context"}, run_id=state.run_id, thread_id=state.work_id)
    state = load_context(state)
    await emit(ctx, "STEP_STARTED", {"node": "classify_intent"}, run_id=state.run_id, thread_id=state.work_id)
    state = classify_intent(state, judge=ctx.judge)
    if state.intent.kind in {"question", "chat"}:
        state = await answer(state, llm=ctx.llm)
        return finalize(state)
    if state.intent.kind == "confirm":
        state.awaiting_confirmation = False
        state.pending_confirmation = False
        state.summary = "已确认采用当前稿，停止自动修复。"
        if state.rendered_path:
            await emit(
                ctx,
                "CUSTOM",
                {"type": "render_done", "format": state.output_mode, "path": state.rendered_path, "revision": state.ir.revision if state.ir else 0},
                run_id=state.run_id,
                thread_id=state.work_id,
            )
        return finalize(state)

    await emit(ctx, "STEP_STARTED", {"node": "ensure_template_spec"}, run_id=state.run_id, thread_id=state.work_id)
    state = ensure_template_spec(state)
    if state.intent.kind == "edit" and not state.intent.target_slots:
        state = classify_intent(state, judge=ctx.judge)

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
            await emit(ctx, "STEP_STARTED", {"node": "gather", "slots": batch}, run_id=state.run_id, thread_id=state.work_id)
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
            passed = [draft for draft, judgement in drafted if judgement.passed]
            if passed:
                state = commit_drafts(state, passed)
            save_checkpoint(state)

        await emit(ctx, "STEP_STARTED", {"node": "render"}, run_id=state.run_id, thread_id=state.work_id)
        state = render_node(state)
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
        if state.rendered_path:
            await emit_json(ctx, "file_changed", Path(state.rendered_path).name)

        await emit(ctx, "STEP_STARTED", {"node": "validate"}, run_id=state.run_id, thread_id=state.work_id)
        state = validate_node(state)
        for issue in state.issues:
            await emit(
                ctx,
                "CUSTOM",
                {"type": "validation_issue", **issue.model_dump()},
                run_id=state.run_id,
                thread_id=state.work_id,
            )

        if state.issues and state.repair_round < state.max_repair_rounds:
            state = triage_issues(state)
            if not state.awaiting_confirmation:
                continue
            await emit(
                ctx,
                "CUSTOM",
                {"type": "awaiting_confirmation", "issues": [issue.model_dump() for issue in state.issues]},
                run_id=state.run_id,
                thread_id=state.work_id,
            )
        break

    state = plan_node(state)
    await persist_plan(state, lambda block, content: emit_json(ctx, block, content))
    state = finalize(state)
    save_checkpoint(state)
    if state.summary and ctx.stream and hasattr(ctx.stream, "print_main_content"):
        await ctx.stream.print_main_content(state.summary)
    return state


async def _draft_and_judge(slot, state: PaperState, ctx: RunContext):
    await emit(ctx, "STEP_STARTED", {"node": "draft", "slot_id": slot.id}, run_id=state.run_id, thread_id=state.work_id)
    draft = await draft_slot(slot, state, ctx)
    judgement = judge_draft(draft, slot, judge=ctx.judge)
    repairs = state.slot_repairs.get(slot.id, 0)
    while not judgement.passed and repairs < state.max_repair_rounds:
        repairs += 1
        state.slot_repairs[slot.id] = repairs
        await emit(ctx, "STEP_STARTED", {"node": "revise", "slot_id": slot.id}, run_id=state.run_id, thread_id=state.work_id)
        draft = heuristic_draft(slot, state)
        judgement = judge_draft(draft, slot, judge=ctx.judge)
    await emit(
        ctx,
        "STEP_FINISHED",
        {"node": "draft", "slot_id": slot.id, "passed": judgement.passed},
        run_id=state.run_id,
        thread_id=state.work_id,
    )
    return draft, judgement
