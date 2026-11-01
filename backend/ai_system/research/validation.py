"""Input-spec integrity and frozen historical context; no metric acceptance gate."""
import hashlib
import json

from .models import Assumption, Requirement


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def report_fingerprint(report):
    return fingerprint({k: v for k, v in report.items() if k not in {"status", "error", "reviews"}})


def validate_spec(source, analysis):
    tasks = {sp["id"] for sp in analysis["subproblems"]}
    requirements = [Requirement.model_validate(r) for r in analysis["requirements"]]
    assumptions = [Assumption.model_validate(a) for a in analysis["assumptions"]]
    errors = []
    if not requirements or len({r.id for r in requirements}) != len(requirements):
        errors.append("要求必须非空且编号唯一")
    if len({a.id for a in assumptions}) != len(assumptions):
        errors.append("假设编号重复")
    missing_quotes = [(r.id, r.source_quote) for r in requirements if r.source_quote not in source]
    if missing_quotes:
        errors.append(f"要求的来源摘录不在原题中：{missing_quotes}；请逐字摘录原题，不要概述或改动数值")
    covered = {key for r in requirements for key in r.task_ids}
    if covered != tasks:
        errors.append(f"每个研究任务都必须对应原题要求，要求不能指向未知任务；未覆盖任务={sorted(tasks-covered)}，"
                         f"未知任务={sorted(covered-tasks)}，实际任务={sorted(tasks)}。完整论文由流水线生成，不要为其添加虚构子问题")
    for owner in [*requirements, *assumptions]:
        if len(set(owner.task_ids)) != len(owner.task_ids) or not set(owner.task_ids) <= tasks:
            errors.append("要求/假设的作用范围无效")
    if errors:
        raise ValueError("\n".join(errors))
    return requirements, assumptions


def bind_requirements(state, requirements):
    """Version current requirement bindings; executed contracts stay in executions."""
    bound = {r['id']:dict(r) for r in state['spec']['requirements']}
    for requirement in requirements:
        incoming = requirement.model_dump()
        previous = bound.get(requirement.id)
        changed = previous is not None and any(previous[k] != v for k,v in incoming.items())
        bound[requirement.id] = {**incoming, 'revision':previous['revision'] + int(changed) if previous else 1}
    return list(bound.values())


def protocol(state, task_id, *, execution_id=None):
    if execution_id:
        execution = state['executions'][execution_id]
        frozen = execution.get('experience_basis', {}).get('protocol')
        if frozen is not None:
            return frozen
    task = state["experiments"][task_id]
    if task.get("verifies_issue"):
        return task["verification_protocol"]
    checks = []
    for kind, owners in (("requirement", state["spec"]["requirements"]), ("assumption", list(state["assumptions"].values()))):
        for owner in owners:
            if kind == "assumption" and owner["status"] == "retired":
                continue
            for check in owner["checks"]:
                if check["left"]["task_id"] == task_id:
                    checks.append({"owner_kind": kind, "owner_id": owner["id"],
                                   "owner_revision": owner["revision"], "check": check})
    return checks
