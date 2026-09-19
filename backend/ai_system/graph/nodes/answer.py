from __future__ import annotations

from langchain_core.messages import HumanMessage

from ...llm import load_prompt
from ..state import PaperState


async def answer(state: PaperState, llm=None) -> PaperState:
    if llm is not None:
        try:
            prompt = load_prompt("answer.md").format(
                user_message=state.user_message,
                plan_summary=_plan_summary(state),
                history=_history_text(state),
            )
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            text = getattr(response, "content", "") or ""
            if text.strip():
                state.messages.append(text.strip())
                state.summary = text.strip()
                return state
        except Exception:
            pass
    state.summary = _fallback_answer(state)
    state.messages.append(state.summary)
    return state


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
