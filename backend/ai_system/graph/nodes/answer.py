from __future__ import annotations

import logging
import uuid

from langchain_core.messages import HumanMessage

from ...llm import load_prompt
from ..state import PaperState

logger = logging.getLogger(__name__)


async def answer(state: PaperState, llm=None, ctx=None) -> PaperState:
    if llm is not None:
        try:
            prompt = load_prompt("answer.md").format(
                user_message=state.user_message,
                plan_summary=_plan_summary(state),
                history=_history_text(state),
            )
            text, streamed = await _generate(prompt, llm, state, ctx)
            if text.strip():
                state.messages.append(text.strip())
                state.summary = text.strip()
                state.streamed_answer = streamed
                return state
        except Exception as exc:
            logger.warning("answer 节点模型调用失败，使用回退回答: %s", exc)
    state.summary = _fallback_answer(state)
    state.messages.append(state.summary)
    return state


async def _generate(prompt: str, llm, state: PaperState, ctx) -> tuple[str, bool]:
    """Stream the reply token by token when the model and stream support it.

    Returns (text, streamed). Streamed text already reached the chat through the
    content channel, so callers should not echo it again as a completion card.
    """
    from ..context import emit

    stream = getattr(ctx, "stream", None) if ctx is not None else None
    can_stream = hasattr(llm, "astream") and stream is not None and hasattr(stream, "print_stream")
    if not can_stream:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        return _message_text(getattr(response, "content", response)), False

    message_id = uuid.uuid4().hex[:12]
    await emit(ctx, "TEXT_MESSAGE_START", {"message_id": message_id, "role": "assistant"}, run_id=state.run_id, thread_id=state.work_id)
    parts: list[str] = []
    async for chunk in llm.astream([HumanMessage(content=prompt)]):
        delta = _message_text(getattr(chunk, "content", chunk))
        if not delta:
            continue
        parts.append(delta)
        await stream.print_stream(delta)
    await emit(
        ctx,
        "TEXT_MESSAGE_END",
        {"message_id": message_id, "text": "".join(parts)},
        run_id=state.run_id,
        thread_id=state.work_id,
    )
    return "".join(parts), True


def _message_text(content) -> str:
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


def _fallback_answer(state: PaperState) -> str:
    last_user = _last_user_turn(state)
    prefix = f"结合上一轮「{_clip(last_user, 40)}」来看，" if last_user and last_user != state.user_message else ""
    if state.plan and state.plan.tasks:
        focus = next((task.title for task in state.plan.tasks if task.status == "pending"), state.plan.tasks[0].title)
        return f"{prefix}当前写作计划共 {len(state.plan.tasks)} 项，下一步是「{focus}」。如需改某一节，直接说章节名即可。"
    return f"{prefix}还没有生成写作计划。发送写作需求后会从模板槽位推导任务并开始起草。"


def _history_text(state: PaperState) -> str:
    if not state.history:
        return "无"
    return "；".join(f"{turn.get('role')}: {(turn.get('content') or '')[:80]}" for turn in state.history[-8:])


def _last_user_turn(state: PaperState) -> str:
    for turn in reversed(state.history):
        if turn.get("role") == "user":
            return (turn.get("content") or "").strip()
    return ""


def _clip(text: str, limit: int) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plan_summary(state: PaperState) -> str:
    if not state.plan:
        return "无计划"
    return "；".join(f"{task.title}={task.status}" for task in state.plan.tasks[:8])
