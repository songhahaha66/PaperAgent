"""Bounded planning: explicit revisions and dependency-ready work selection.

The original question is immutable. A human revision augments the selected
objectives; it cannot silently remove requirements or relax the validator.
"""
from .models import PlanPatch, TaskChange


def propose_revision(snapshot, command_id, base_revision, change, scope):
    if not change.strip():
        raise ValueError("修改内容不能为空")
    selected = list(snapshot["tasks"]) if scope is None else scope
    if not selected or len(set(selected)) != len(selected) or not set(selected) <= set(snapshot["tasks"]):
        raise ValueError("修改范围包含未知或重复任务")
    return PlanPatch(command_id=command_id, base_revision=base_revision, reason=change,
                     changes=[TaskChange(task_id=key, objective=snapshot["tasks"][key]["objective"]
                                         + "\n\n补充要求：" + change) for key in selected])


def ready_tasks(snapshot):
    tasks = snapshot["tasks"]
    if len(snapshot["attempts"]) >= snapshot["max_total_attempts"]:
        return []
    return [key for key, task in tasks.items() if task["execution_status"] == "pending" and
            sum(a["task_id"] == key and a["revision"] == task["revision"] for a in snapshot["attempts"].values()) < snapshot["max_attempts"] and
            all(tasks[dep]["acceptance"] == "accepted" and tasks[dep]["validity"] == "current"
                for dep in task["dependencies"])]


class Planner:
    """Suggest bounded plan patches from confirmed validation failures.

    Service failures stay blocked for explicit retries. A semantic suspicion
    alone never invalidates accepted evidence.
    """
    def __init__(self, llm):
        self.llm = llm

    async def propose(self, snapshot):
        import json
        from ..graph.llm_utils import structured_model, as_messages
        from .models import PlanProposal
        context = {"spec": snapshot["spec"], "revision": snapshot["revision"],
                   "tasks": [{k: t[k] for k in ("task_id", "objective", "dependencies", "acceptance")}
                             for t in snapshot["tasks"].values()], "issues": snapshot["issues"],
                   "remaining_attempts": snapshot["max_total_attempts"] - len(snapshot["attempts"])}
        prompt = (
            "你是研究全局规划器。根据已确认的验收问题建议最小计划修改，必要时补充验证任务并调整依赖。"
            "保留原题所有要求和硬约束，不得放宽验证标准。只提议，不宣布任务完成。"
            "evidence_refs 填触发问题的任务编号；没有足够依据时 changes 留空。"
            "revise 的 objective 写补充策略；add 的 objective 写新任务目标。不得重复无新证据的失败路线。"
        )
        response = await structured_model(self.llm, PlanProposal).ainvoke(as_messages(prompt, json.dumps(context, ensure_ascii=False)))
        return response if isinstance(response, PlanProposal) else PlanProposal.model_validate(response)
