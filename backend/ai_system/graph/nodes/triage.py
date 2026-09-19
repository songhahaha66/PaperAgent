from __future__ import annotations

from ...schemas.plan import WRITABLE_ROLES
from ..state import PaperState


def triage_issues(state: PaperState) -> PaperState:
    affected = [issue.slot_id for issue in state.issues if issue.slot_id]
    if not affected and state.spec:
        affected = [slot.id for slot in state.spec.slots if slot.role in WRITABLE_ROLES]
    retryable = []
    for slot_id in affected:
        used = state.slot_repairs.get(slot_id, 0)
        if used < state.max_repair_rounds:
            state.slot_repairs[slot_id] = used + 1
            retryable.append(slot_id)
    if retryable:
        state.intent.kind = "edit"
        state.intent.target_slots = retryable
        state.repair_round += 1
        state.awaiting_confirmation = False
    else:
        state.awaiting_confirmation = True
    return state
