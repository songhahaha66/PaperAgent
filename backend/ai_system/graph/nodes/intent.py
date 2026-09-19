from __future__ import annotations

import re

from ...judge.heuristic import HeuristicJudge
from ...schemas.intent import EditIntent
from ...schemas.plan import WRITABLE_ROLES
from ..state import PaperState

WRITE_RE = re.compile(r"(写|生成|完成|起草|补充|继续)")
EDIT_RE = re.compile(r"(修改|改一下|重写|更新|调整)")
QUESTION_RE = re.compile(r"(什么|为什么|怎么|如何|吗|？|\?)")
SECTION_RE = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*[章节部分]")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def classify_intent(state: PaperState, judge=None) -> PaperState:
    message = (state.user_message or "").strip()
    writable = [
        slot.id
        for slot in (state.spec.slots if state.spec else [])
        if slot.role in WRITABLE_ROLES
    ]
    if not writable and state.plan:
        writable = [task.slot_id for task in state.plan.tasks]

    kind = "write"
    if EDIT_RE.search(message):
        kind = "edit"
    elif QUESTION_RE.search(message) and (
        message.endswith("？") or message.endswith("?") or (len(message) < 40 and not WRITE_RE.search(message))
    ):
        kind = "question" if len(message) < 80 else "chat"

    targets = _target_slots(message, writable, state)
    if kind == "write":
        targets = []
    state.intent = EditIntent(kind=kind, target_slots=targets, reason="heuristic")
    if judge and not isinstance(judge, HeuristicJudge):
        try:
            answers = judge.ask({"message": message, "slots": writable}, {})
            value = getattr(answers.get("kind"), "value", None)
            if value in {"write", "edit", "question", "chat"}:
                state.intent.kind = value
                state.intent.reason = "judge"
        except Exception:
            pass
    return state


def _target_slots(message: str, writable: list[str], state: PaperState) -> list[str]:
    match = SECTION_RE.search(message)
    if match and writable:
        raw = match.group(1)
        index = CN_NUM.get(raw) or (int(raw) if raw.isdigit() else 0)
        if 1 <= index <= len(writable):
            return [writable[index - 1]]
    if state.spec:
        hits = [
            slot.id
            for slot in state.spec.slots
            if slot.role in WRITABLE_ROLES and slot.title and slot.title[:8] in message
        ]
        if hits:
            return hits
    return []
