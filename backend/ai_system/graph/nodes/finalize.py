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
    if state.summary and state.summary not in state.messages:
        state.messages.append(state.summary)
    state.finished = True
    return state
