"""Task-scoped premise proposals; only the coordinator registers them."""
from .models import AssumptionDraft, ResearchConflict


def assumption_id(task_id, key):
    return f"A:{task_id}:{key}"


def premise_definition(task_id, draft):
    aid = assumption_id(task_id, draft.key)
    return {"statement": draft.statement,
            "checks": [{**c.model_dump(), "id": f"{aid}:{c.id}"} for c in draft.checks]}


def assumption_scope(tasks, root):
    scope = {root}
    while True:
        more = {key for key, task in tasks.items()
                if task["kind"] != "verify" and set(task["dependencies"]) & scope}
        if more <= scope:
            return [root, *sorted(scope - {root})]
        scope |= more


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
            scope = contract.get("assumption_task_ids", [task_id])
            tasks = contract.get("task_ids", scope)
            check_ids = set()
            for check in draft.checks:
                if check.id in check_ids:
                    raise ValueError(f"假设 {aid} 的检查编号重复：{check.id}")
                check_ids.add(check.id)
                if check.left.task_id not in scope:
                    raise ValueError(f"假设 {aid} 的检查 {check.id} 指向作用范围外的任务 {check.left.task_id}；允许的任务编号：{scope}，请使用原始编号")
                unknown = {selector.task_id for selector in check.selectors()} - set(tasks)
                if unknown:
                    raise ValueError(f"假设 {aid} 的检查 {check.id} 指向未知任务 {sorted(unknown)}；实际任务编号：{tasks}")
            pending.append(draft)
    return pending
