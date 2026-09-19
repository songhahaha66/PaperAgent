from __future__ import annotations

from ..state import PaperState


def finalize(state: PaperState) -> PaperState:
    if state.issues:
        details = "；".join(issue.detail or issue.code for issue in state.issues[:4])
        prefix = "仍有校验问题，已暂停等待确认：" if state.awaiting_confirmation else "已写出初稿，但校验还有问题："
        state.summary = state.summary or f"{prefix}{details}"
    elif not state.summary:
        if state.intent.kind in {"question", "chat"}:
            state.summary = state.messages[-1] if state.messages else "已回答。"
        elif state.intent.kind == "confirm":
            state.summary = state.summary or "已确认采用当前稿。"
        else:
            written = len(state.ir.sections) if state.ir else 0
            state.summary = f"已按模板槽位完成写作，写入 {written} 个内容块，并渲染到 {state.rendered_path or '工作区'}。"
    if state.failed_slots and state.intent.kind not in {"question", "chat", "confirm"}:
        names = "、".join(_slot_title(state, slot_id) for slot_id in list(state.failed_slots)[:4])
        more = f" 等 {len(state.failed_slots)} 项" if len(state.failed_slots) > 4 else ""
        state.summary = f"{state.summary} 其中 {names}{more}未能生成合格草稿，仍为待写状态，可稍后重试。"
    if state.summary and state.summary not in state.messages:
        state.messages.append(state.summary)
    state.finished = True
    return state


def _slot_title(state: PaperState, slot_id: str) -> str:
    if state.spec:
        for slot in state.spec.slots:
            if slot.id == slot_id:
                return f"「{slot.title or slot_id}」"
    return f"「{slot_id}」"
