"""Executable acceptance rules; model scores cannot override these checks."""
import hashlib
import json
import math
import re

from .models import Assumption, DomainCheck, Requirement, ValidationRecord, CheckResult


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def report_fingerprint(report):
    return fingerprint({k: v for k, v in report.items() if k not in {"status", "error", "reviews"}})


SOURCE_TOKEN = re.compile(r"[A-Za-z]+|\d+(?:\.\d+)?|[^\s]", re.UNICODE)


def source_excerpt(source, quote):
    """Resolve whitespace-only copying differences to an actual original span.

    Numbers and Latin words remain whole tokens: '1 5' cannot become '15', nor
    'not possible' become 'notpossible'. Stored contracts still use exact text.
    """
    if quote and quote in source:
        return quote
    spans = list(SOURCE_TOKEN.finditer(source))
    tokens = [m.group() for m in spans]
    wanted = SOURCE_TOKEN.findall(quote)
    if wanted:
        for index in range(len(tokens) - len(wanted) + 1):
            if tokens[index:index + len(wanted)] == wanted:
                return source[spans[index].start():spans[index + len(wanted) - 1].end()]
    return None


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
    check_ids = []
    for owner in [*requirements, *assumptions]:
        if len(set(owner.task_ids)) != len(owner.task_ids) or not set(owner.task_ids) <= tasks:
            errors.append("要求/假设的作用范围无效")
        for check in owner.checks:
            check_ids.append(check.id)
            if isinstance(owner, Requirement) and check.expected is not None:
                # A source quote is not permission to invent an accuracy target.
                # Match whole numeric tokens (including percentages), not digits
                # elsewhere in the original question or arbitrary string subsets.
                numbers = [float(m[0]) / (100 if m[1] else 1) for m in re.findall(
                    r"(?<![A-Za-z0-9_.])([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([%％]?)", owner.source_quote)]
                explicit_zero = check.expected == 0 and re.search(r"非负|非正|大于零|小于零|non[- ]negative|non[- ]positive", owner.source_quote, re.I)
                if not explicit_zero and check.expected not in numbers:
                    errors.append(f"要求 {owner.id} 的检查 {check.id} 阈值 {check.expected:g} 缺少该要求原文中的数值依据；"
                                  "不得为比较/评估任务添加原题没有的误差或收益门槛。使用finite检查真实指标，或把方法前提作为假设，不要改写原题要求")
            if check.left.task_id not in owner.task_ids:
                errors.append(f"验收检查必须属于要求/假设的作用范围：{owner.id} 的任务为 {owner.task_ids}，"
                                 f"检查 {check.id} 却指向 {check.left.task_id}，请核对任务归属")
            if check.left.task_id not in tasks or (check.right and check.right.task_id not in tasks):
                errors.append("验收检查指向未知任务")
            if check.linear_model:
                model = check.linear_model
                quotes = [model.objective_quote, *(v.source_quote for v in model.variables),
                          *(c.source_quote for c in model.constraints)]
                if any(quote not in source for quote in quotes):
                    missing = [quote for quote in quotes if quote not in source]
                    errors.append(f"线性模型的目标、变量和约束必须逐字引用原题：检查 {check.id} 的以下摘录不存在：{missing}。"
                                     "线性趋势/回归等方法名称不代表原题给出了线性规划合同；不能编造目标及约束，请使用适用的验收检查")
    if len(set(check_ids)) != len(check_ids):
        errors.append("验收检查编号必须全局唯一")
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


def _number(value):
    text = str(value).strip().replace(",", "")
    number = float(text.rstrip("%")) / (100 if text.endswith("%") else 1)
    if not math.isfinite(number):
        raise ValueError("结果不是有限数")
    return number


def requirement_evidence_gaps(requirement_ids, proofs, available_refs):
    """Shared submission/commit checks for the frozen requirement bindings."""
    supplied = {}
    for proof in proofs:
        supplied.setdefault(proof["requirement_id"], set()).update(proof["evidence_refs"])
    gaps = {rid: f"要求 {rid} 不属于本任务的冻结契约" for rid in supplied if rid not in requirement_ids}
    for rid in requirement_ids:
        refs = supplied.get(rid, set())
        if not refs or not refs <= available_refs:
            gaps[rid] = f"要求 {rid} 缺少有效登记证据映射，请在 requirement_evidence 中填写本问或依赖任务的实际登记编号"
    return gaps


def evaluate(state, task_id, results):
    from .domain import current_results
    task = state['experiments'][task_id]
    rules = protocol(state, task_id, execution_id=task['execution_id'])
    available = current_results(state, task_id=task_id, results=results)
    checks = []
    for rule in rules:
        check = DomainCheck.model_validate(rule["check"])
        evidence = []

        def select(selector, override=False):
            sid = task_id if override and task.get("verifies_issue") else selector.task_id
            found = [r for r in available if r["subproblem"] == sid and r["name"] == selector.name]
            if len(found) != 1:
                raise ValueError(f"{sid} 缺少唯一登记结果「{selector.name}」")
            evidence.append(found[0]["id"])
            return found[0]["value"]

        try:
            raw = select(check.left, override=True)
            detail = ""
            if check.operator == "linear_feasible":
                from .linear import verify_linear
                values = [_number(select(selector, override=True)) for selector in check.selectors()[1:]]
                passed, detail = verify_linear(check.linear_model, values, _number(raw), check.tolerance)
            elif check.operator == "exists":
                passed = bool(str(raw).strip())
            else:
                left = _number(raw)
                right = _number(select(check.right, override=check.right.task_id == check.left.task_id)) if check.right else check.expected
                passed = {
                    "finite": lambda: True, "le": lambda: left <= right + check.tolerance,
                    "lt": lambda: left < right, "ge": lambda: left >= right - check.tolerance,
                    "gt": lambda: left > right, "eq": lambda: left == right,
                    "approx": lambda: abs(left - right) <= check.tolerance,
                }[check.operator]()
            if passed and task.get("verifies_issue"):
                original = select(check.left)
                if check.operator == "exists":
                    agrees = str(raw).strip() == str(original).strip()
                else:
                    agrees = math.isclose(_number(raw), _number(original), rel_tol=1e-9, abs_tol=check.tolerance)
                if check.linear_model:
                    agrees = agrees and all(math.isclose(_number(select(s, override=True)), _number(select(s)),
                                                        rel_tol=1e-9, abs_tol=check.tolerance) for s in check.selectors()[1:])
                if not agrees:
                    checks.append(CheckResult(check_id=check.id, outcome="failed", evidence_refs=evidence,
                        message=f"{check.description}：独立复算值 {raw} 与原登记值 {original} 不一致",
                        **{key: rule[key] for key in ("owner_kind", "owner_id", "owner_revision")}))
                    continue
            outcome = "passed" if passed else "failed"
            message = f"{check.description}：{detail or ('通过' if passed else '违反验收条件')}"
        except (ValueError, TypeError, OverflowError) as exc:
            outcome, message = "inconclusive", f"{check.description}：{exc}"
        checks.append(CheckResult(check_id=check.id, outcome=outcome, evidence_refs=evidence, message=message,
            **{key: rule[key] for key in ("owner_kind", "owner_id", "owner_revision")}))
    outcomes = {c.outcome for c in checks}
    return ValidationRecord(protocol_hash=fingerprint(rules), checks=checks,
        outcome="rejected" if "failed" in outcomes else "inconclusive" if "inconclusive" in outcomes else "accepted")
