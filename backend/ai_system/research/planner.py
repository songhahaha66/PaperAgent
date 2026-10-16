"""Bounded planning over immutable requirements and evidence-backed issues."""
import json

from .models import Contract, Requirement, PlanPatch, TaskChange, PlanProposal


class AmendmentSpec(Contract):
    requirements: list[Requirement]


def propose_revision(snapshot, command_id, base_revision, change, scope, requirements):
    selected = list(snapshot["tasks"]) if scope is None else scope
    selected = [key for key in selected if snapshot["tasks"][key]["kind"] != "verify"]
    if not change.strip() or not selected or len(set(selected)) != len(selected):
        raise ValueError("修改内容和范围必须有效")
    return PlanPatch(command_id=command_id, base_revision=base_revision, reason=change,
        changes=[TaskChange(task_id=key, objective=snapshot["tasks"][key]["objective"] + "\n\n补充要求：" + change,
            requirement_ids=snapshot["tasks"][key]["requirement_ids"] + [r.id for r in requirements if key in r.task_ids]) for key in selected],
        requirements=requirements, amendment=change)


def ready_tasks(snapshot):
    tasks = snapshot["tasks"]
    if len(snapshot["attempts"]) >= snapshot["max_total_attempts"]:
        return []
    blocked = {key for i in snapshot["issues"] if i["status"] == "confirmed" and i["action"] == "recompute"
               for key in i["repair_targets"]}
    return [key for key, task in tasks.items() if not task["retired"] and key not in blocked
            and task["execution_status"] == "pending"
            and sum(a["task_id"] == key and a["revision"] == task["revision"] for a in snapshot["attempts"].values()) < snapshot["max_attempts"]
            and all(snapshot["assumptions"][aid]["status"] != "refuted" for aid in task["assumption_ids"])
            and all(tasks[dep]["acceptance"] == "accepted" and tasks[dep]["validity"] == "current" for dep in task["dependencies"])]


class Planner:
    def __init__(self, llm):
        self.llm = llm

    async def amendment(self, snapshot, change, scope):
        from ..graph.llm_utils import structured_model, as_messages
        selected = scope if scope is not None else [key for key, t in snapshot["tasks"].items() if t["kind"] != "verify"]
        payload = {"change": change, "scope": selected, "used_requirement_ids": [r["id"] for r in snapshot["spec"]["requirements"]]}
        response = await structured_model(self.llm, AmendmentSpec).ainvoke(as_messages(
            "把用户补充要求转成结构化要求，使用新的RQ编号，source_quote逐字摘录change，task_ids只取scope中的任务。"
            "硬约束必须有可执行检查，检查编号全局唯一。原题约束保持不变；不要悄悄删除或放宽。", json.dumps(payload, ensure_ascii=False)))
        contract = response if isinstance(response, AmendmentSpec) else AmendmentSpec.model_validate(response)
        if not contract.requirements or {key for r in contract.requirements for key in r.task_ids} != set(selected):
            raise ValueError("补充要求未覆盖修改范围")
        return contract.requirements

    async def propose(self, snapshot):
        from ..graph.llm_utils import structured_model, as_messages
        context = {"spec": snapshot["spec"], "revision": snapshot["revision"], "assumptions": snapshot["assumptions"],
            "tasks": [{k: t[k] for k in ("task_id", "revision", "objective", "dependencies", "requirement_ids", "assumption_ids", "acceptance")}
                      for t in snapshot["tasks"].values() if not t["retired"]],
            "issues": [i for i in snapshot["issues"] if i["status"] == "confirmed" and i["action"] == "recompute"],
            "validations": snapshot["validations"][-8:],
            "evidence": [{"id": k, **v["payload"]} for k, v in snapshot["evidence"].items()
                         if any(k in i["evidence_refs"] for i in snapshot["issues"] if i["status"] == "confirmed")],
            "remaining_attempts": snapshot["max_total_attempts"] - len(snapshot["attempts"])}
        prompt = (
            "你是研究全局规划器。仅修复已确认的研究问题，issue_ids必须引用上下文问题编号。"
            "可以修改上游任务并重算其下游，或增加绑定现有requirement_ids的研究工作。"
            "被反证的假设必须用assumptions修订，指定base_revision和替代前提/可证伪检查，或显式retired；不能继续沿用。"
            "不得删除任何原题要求、放宽要求的检查、把疑点当成事实、或仅改写文字掩盖模型错误。"
            "revise.objective写补充研究策略，保留原始目标。证据不足或没有可行路线时changes与assumptions留空。"
        )
        response = await structured_model(self.llm, PlanProposal).ainvoke(as_messages(prompt, json.dumps(context, ensure_ascii=False)))
        return response if isinstance(response, PlanProposal) else PlanProposal.model_validate(response)
