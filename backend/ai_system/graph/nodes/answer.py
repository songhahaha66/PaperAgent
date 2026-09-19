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
    if state.plan and state.plan.tasks:
        focus = next((task.title for task in state.plan.tasks if task.status == "pending"), state.plan.tasks[0].title)
        return f"当前写作计划共 {len(state.plan.tasks)} 项，下一步是「{focus}」。如需改某一节，直接说章节名即可。"
    return "还没有生成写作计划。发送写作需求后会从模板槽位推导任务并开始起草。"


def _plan_summary(state: PaperState) -> str:
    if not state.plan:
        return "无计划"
    return "；".join(f"{task.title}={task.status}" for task in state.plan.tasks[:8])
