"""Task-scoped premise proposals; only the coordinator registers them."""
from .models import AssumptionDraft, ResearchConflict


def assumption_id(task_id, key):
    return f"A:{task_id}:{key}"


def premise_definition(task_id, draft):
    aid = assumption_id(task_id, draft.key)
    return {"statement": draft.statement,
            "checks": [{**c.model_dump(), "id": f"{aid}:{c.id}"} for c in draft.checks]}


def unbound_assumptions(task_id, drafts, contract):
    existing = {a["id"]: a for a in contract.get("assumptions", [])}
    pending = []
    seen = set()
    for raw in drafts:
        draft = AssumptionDraft.model_validate(raw)
        aid = assumption_id(task_id, draft.key)
        if aid in seen:
            raise ValueError("假设键重复")
        seen.add(aid)
        known = existing.get(aid)
        if known:
            if any(known[k] != v for k, v in premise_definition(task_id, draft).items()):
                raise ResearchConflict("不能静默替换已有关键假设，请报告带证据的问题并修订计划")
        else:
            pending.append(draft)
    return pending
