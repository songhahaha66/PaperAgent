from __future__ import annotations

from ...schemas.plan import WRITABLE_ROLES
from ..state import PaperState

# Issues that a fresh draft of the same slot can plausibly fix. Skeleton/style/furniture
# problems are renderer or template problems; rewriting prose would not change them.
CONTENT_FIXABLE = {
    "placeholder_too_short",
    "example_left",
    "figure_missing",
    "citation_unresolved",
    "table_missing",
}


def triage_issues(state: PaperState) -> PaperState:
    """Decide between another local repair round and asking the human.

    Only slot-scoped, content-fixable errors on writable slots are retried, and each
    slot gets at most `max_repair_rounds` repairs. When nothing is retryable but
    errors remain, the loop stops and the issues are surfaced for confirmation.
    """
    writable = {slot.id for slot in (state.spec.slots if state.spec else []) if slot.role in WRITABLE_ROLES}
    errors = [issue for issue in state.issues if issue.severity == "error"]
    retryable: list[str] = []
    budget_left = state.repair_round < state.max_repair_rounds
    for issue in errors if budget_left else []:
        slot_id = issue.slot_id or ""
        if issue.code not in CONTENT_FIXABLE or slot_id not in writable or slot_id in retryable:
            continue
        if state.slot_repairs.get(slot_id, 0) < state.max_repair_rounds:
            retryable.append(slot_id)

    if retryable:
        for slot_id in retryable:
            state.slot_repairs[slot_id] = state.slot_repairs.get(slot_id, 0) + 1
        state.intent.kind = "edit"
        state.intent.target_slots = retryable
        state.repair_round += 1
        state.awaiting_confirmation = False
    else:
        state.awaiting_confirmation = bool(errors)
    return state
