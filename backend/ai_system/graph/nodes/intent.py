from __future__ import annotations

import re

from ...judge.heuristic import CONFIRM_RE, HeuristicJudge, infer_intent_kind
from ...judge.questions.intent import intent_questions
from ...schemas.intent import EditIntent
from ...schemas.plan import WRITABLE_ROLES
from ..state import PaperState

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

    kind = infer_intent_kind(message)
    if state.pending_confirmation and CONFIRM_RE.search(message):
        kind = "confirm"
    targets = _target_slots(message, writable, state)
    if kind == "write":
        targets = []
    state.intent = EditIntent(kind=kind, target_slots=targets, reason="heuristic")
    if judge is not None:
        try:
            answers = judge.ask({"message": message, "slots": writable}, intent_questions())
            value = getattr(answers.get("kind"), "value", None)
            if value in {"write", "edit", "question", "chat", "confirm"}:
                state.intent.kind = value
                state.intent.reason = "judge" if not isinstance(judge, HeuristicJudge) else "heuristic"
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
