"""P2 contracts, counterexamples, causal replanning and delivery gates."""

from ai_system.research.domain import findings as research_findings
import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.research.models import (
    Assumption, AssumptionChange, DomainCheck, IssueReport, PlanPatch,
    Requirement, ResultSelector, TaskChange, WorkerResult, ResearchConflict,
)
from ai_system.research.store import ResearchStore
from ai_system.research.planner import ready_tasks
from ai_system.graph.agent import PaperGraphAgent
from ai_system.graph.state import ProblemAnalysis, Subproblem, ConsistencyReview, ClaimAudit, ResultReview
from test_paper_graph import FakeModel, FixedJudge

TASK = "Estimate demand; optimize allocation with capacity <= 10; also compute an independent statistic."


def problem():
    return ProblemAnalysis(title="需求与容量约束研究", restatement=TASK, overall_analysis="estimate, validate, allocate",
        subproblems=[Subproblem(id="Q1", title="需求估计", question="Estimate demand"),
                     Subproblem(id="Q2", title="容量配置", question="Validate demand and allocate", depends_on=["Q1"]),
                     Subproblem(id="Q3", title="独立统计", question="Independent statistic")],
        requirements=[Requirement(id="RQ1", statement="estimate demand", source_quote="Estimate demand", kind="goal", task_ids=["Q1"]),
                      Requirement(id="RQ2", statement="respect capacity", source_quote="capacity <= 10", kind="constraint", task_ids=["Q2"],
                          checks=[DomainCheck(id="capacity", description="容量不超过上限", left=ResultSelector(task_id="Q2", name="allocation"), operator="le", expected=10)]),
                      Requirement(id="RQ3", statement="independent statistic", source_quote="independent statistic", kind="goal", task_ids=["Q3"])],
        assumptions=[Assumption(id="A1", statement="Mean demand is representative", task_ids=["Q1", "Q2"],
            checks=[DomainCheck(id="representative", description="估计应与独立校核一致", left=ResultSelector(task_id="Q2", name="residual"), operator="le", expected=0)])])


def store_for(tmp_path):
    store = ResearchStore(tmp_path, "p2")
    store.initialize(TASK, problem().model_dump())
    return store


def submit(store, key, values, *, order=None, issues=()):
    order = order or store.claim(key)
    results = [{"id": store.allocate_ref("R"), "subproblem": key, "kind": "number", "name": name,
                "value": str(value), "run_id": "run_test"} for name, value in values.items()]
    result = WorkerResult(order=order, execution_status="succeeded", acceptance="accepted",
        report={"status": "completed", "summary": "completed", "requirement_evidence": [
            {"requirement_id": rid, "evidence_refs": [results[0]["id"]]} for rid in store.snapshot()["tasks"][key]["requirement_ids"]]},
        results=results, issues=list(issues))
    return store.commit(result), results


def repair_patch(state):
    return PlanPatch(command_id="repair", base_revision=state["revision"], reason="use robust demand estimator",
        issue_ids=[i["id"] for i in research_findings(state) if i["status"] == "confirmed"],
        changes=[TaskChange(task_id="Q1", objective="Use median demand")],
        assumptions=[AssumptionChange(assumption_id="A1", base_revision=1, statement="Median demand is representative",
            task_ids=["Q1", "Q2"], checks=problem().assumptions[0].checks)])


def test_domain_failure_overrides_worker_acceptance_and_revokes_assumption_consumers(tmp_path):
    store = store_for(tmp_path)
    submit(store, "Q1", {"demand": 12})
    submit(store, "Q3", {"independent": 3})
    unrelated = store.snapshot()["tasks"]["Q3"]["active_attempt"]
    submit(store, "Q2", {"allocation": 12, "residual": 4})
    snapshot = store.snapshot()
    attempt = next(a for a in snapshot["attempts"].values() if a["task_id"] == "Q2")
    assert attempt["execution_status"] == "succeeded" and attempt["acceptance"] == "rejected"
    assert snapshot["assumptions"]["A1"]["status"] == "refuted"
    assert not ready_tasks(snapshot)
    assert {r["subproblem"] for r in store.registry_payload()["results"]} == {"Q3"}
    assert snapshot["tasks"]["Q3"]["active_attempt"] == unrelated
    assert all(i["status"] == "confirmed" and i["evidence_refs"] for i in research_findings(snapshot))
    store.apply_patch(repair_patch(snapshot))
    assert ready_tasks(store.snapshot()) == ["Q1"]
    submit(store, "Q1", {"demand": 8})
    assert ready_tasks(store.snapshot()) == ["Q2"]
    submit(store, "Q2", {"allocation": 8, "residual": 0})
    assert all(i["status"] == "resolved" for i in research_findings(store.snapshot()))
    assert store.snapshot()["assumptions"]["A1"]["revision"] == 2
    assert store.delivery_ready(store.snapshot())


def test_missing_metric_is_inconclusive_and_cannot_publish(tmp_path):
    store = store_for(tmp_path)
    submit(store, "Q1", {"demand": 8})
    submit(store, "Q2", {"allocation": 8})
    attempt = list(store.snapshot()["attempts"].values())[-1]
    assert attempt["acceptance"] == "inconclusive"
    assert store.snapshot()["assumptions"]["A1"]["status"] == "active"
    with pytest.raises(ResearchConflict):
        store.publish_paper("pretend everything is valid")


def test_suspected_issue_preserves_current_results_until_independent_countercheck(tmp_path):
    store = store_for(tmp_path)
    _, refs = submit(store, "Q1", {"demand": 8})
    submit(store, "Q2", {"allocation": 8, "residual": 0})
    before = store.registry_payload()
    store.report_issues([IssueReport(task_id="Q1", category="assumption", assumption_id="A1", message="independent check required",
        check_ids=["representative"], evidence_refs=[refs[0]["id"]])], "review")
    assert store.registry_payload() == before
    [verification] = store.schedule_investigations()
    assert store.registry_payload() == before
    submit(store, verification, {"residual": 4})
    assert store.snapshot()["assumptions"]["A1"]["status"] == "refuted"
    assert store.snapshot()["tasks"][verification]["retired"]
    assert store.registry_payload()["results"] == []


def test_false_suspicion_is_dismissed_and_uncheckable_claim_never_becomes_truth(tmp_path):
    store = store_for(tmp_path)
    _, refs = submit(store, "Q1", {"demand": 8})
    submit(store, "Q2", {"allocation": 8, "residual": 0})
    report = IssueReport(task_id="Q1", category="assumption", assumption_id="A1", check_ids=["representative"],
                        message="suspected conflict", evidence_refs=[refs[0]["id"]])
    store.report_issues([report], "review")
    [verification] = store.schedule_investigations()
    submit(store, verification, {"residual": 0})
    assert research_findings(store.snapshot())[0]["status"] == "dismissed"
    assert store.report_issues([report], "review") == []
    before = store.registry_payload()
    store.report_issues([IssueReport(task_id="Q1", category="model", message="uncheckable statement", evidence_refs=[refs[0]["id"]])], "review")
    assert not store.schedule_investigations()
    assert research_findings(store.snapshot())[-1]["status"] == "inconclusive"
    assert store.registry_payload() == before
    assert not store.delivery_ready(store.snapshot())


def test_assumption_change_rejects_inflight_attempt_and_cannot_weaken_requirements(tmp_path):
    store = store_for(tmp_path)
    submit(store, "Q1", {"demand": 8})
    running = store.claim("Q2")
    # A second confirmed counterexample comes from an independent verification.
    store.report_issues([IssueReport(task_id="Q1", category="assumption", assumption_id="A1", check_ids=["representative"], message="check again")], "review")
    # Direct task edits cannot silently replace the user's capacity contract.
    with pytest.raises(ValueError):
        store.apply_patch(PlanPatch(command_id="drop", base_revision=1, reason="drop requirement",
            changes=[TaskChange(task_id="Q2", objective="skip capacity", requirement_ids=[])]))
    assert store.snapshot()["spec"]["requirements"][1]["checks"][0]["expected"] == 10
    store.apply_patch(PlanPatch(command_id="edit", base_revision=1, reason="new objective",
                               changes=[TaskChange(task_id="Q2", objective="new objective")]))
    receipt, _ = submit(store, "Q2", {"allocation": 8, "residual": 0}, order=running)
    assert not receipt["published"]


def test_source_and_check_contracts_fail_closed(tmp_path):
    analysis = problem().model_dump()
    analysis["requirements"][0]["source_quote"] = "not in original"
    with pytest.raises(ValueError, match="摘录"):
        ResearchStore(tmp_path, "invalid").initialize(TASK, analysis)
    with pytest.raises(ValueError):
        DomainCheck(id="bad", description="bad", left=ResultSelector(task_id="Q1", name="x"), operator="le")
    with pytest.raises(ValueError):
        Requirement(id="bad", statement="capacity", source_quote="capacity", kind="constraint", task_ids=["Q1"])


@pytest.mark.parametrize("operator,value,expected,right,tolerance,outcome", [
    ("finite", "nan", None, False, 0, "inconclusive"),
    ("finite", "inf", None, False, 0, "inconclusive"),
    ("approx", "80%", 0.8, False, 0.001, "accepted"),
    ("approx", "8.01", 8, False, 0.02, "accepted"),
    ("lt", "8", 8, False, 0, "rejected"),
    ("gt", "8", 7, False, 0, "accepted"),
    ("eq", "8", None, True, 0, "accepted"),
    ("ge", "7", None, True, 0, "rejected"),
])
def test_numeric_domain_checks_use_fixed_thresholds_and_dependency_evidence(tmp_path, operator, value, expected, right, tolerance, outcome):
    analysis = problem()
    analysis.assumptions = []
    analysis.requirements[1].checks = [DomainCheck(id="numeric", description="fixed domain rule",
        left=ResultSelector(task_id="Q2", name="allocation"), operator=operator, expected=expected,
        right=ResultSelector(task_id="Q1", name="demand") if right else None, tolerance=tolerance)]
    store = ResearchStore(tmp_path, "numeric")
    source = TASK
    if expected is not None:
        analysis.requirements[1].source_quote = f"Fixed numeric threshold {expected}"
        source += " " + analysis.requirements[1].source_quote
    store.initialize(source, analysis.model_dump())
    _, original = submit(store, "Q1", {"demand": 8})
    submit(store, "Q2", {"allocation": value})
    validation = store.snapshot()["validations"][-1]
    assert validation["outcome"] == outcome
    if right:
        assert original[0]["id"] in validation["checks"][0]["evidence_refs"]


def test_verification_execution_failure_retries_without_refuting_assumption(tmp_path):
    store = store_for(tmp_path)
    submit(store, "Q1", {"demand": 8})
    submit(store, "Q2", {"allocation": 8, "residual": 0})
    store.report_issues([IssueReport(task_id="Q1", category="assumption", assumption_id="A1", check_ids=["representative"], message="verify")], "review")
    [key] = store.schedule_investigations()
    store.commit(WorkerResult(order=store.claim(key), execution_status="failed", acceptance="inconclusive", report={},
        issues=[IssueReport(task_id=key, category="execution", message="kernel stopped")]))
    assert store.snapshot()["assumptions"]["A1"]["status"] == "active"
    store.recover()
    assert key in ready_tasks(store.snapshot())
    assert not store.schedule_investigations(), "recovery reuses the bounded investigation task"
    submit(store, key, {"residual": 0})
    assert all(i["status"] in {"dismissed", "resolved"} for i in research_findings(store.snapshot()))


def test_repair_must_change_issue_cause_and_worker_cannot_cite_unseen_history(tmp_path):
    store = store_for(tmp_path)
    submit(store, "Q1", {"demand": 12})
    submit(store, "Q2", {"allocation": 12, "residual": 4})
    before = store.snapshot()
    with pytest.raises(ValueError, match="上游"):
        store.apply_patch(PlanPatch(command_id="unrelated", base_revision=1, reason="pretend repaired",
            changes=[TaskChange(task_id="Q3", objective="unrelated change")],
            issue_ids=[i["id"] for i in research_findings(before)]))
    assert store.snapshot() == before
    old_ref = next(iter(before["evidence"]))
    with pytest.raises(ValueError, match="冻结输入"):
        submit(store, "Q3", {"independent": 3}, issues=[IssueReport(task_id="Q1", category="model", message="unseen evidence", evidence_refs=[old_ref])])


def test_replacing_refuted_assumption_retires_pending_counterchecks_of_old_premise(tmp_path):
    store = store_for(tmp_path)
    submit(store, "Q1", {"demand": 12})
    submit(store, "Q2", {"allocation": 12, "residual": 4})
    store.report_issues([IssueReport(task_id="Q1", category="assumption", assumption_id="A1", check_ids=["representative"],
        message="second reviewer suspects the same premise")], "review")
    [verification] = store.schedule_investigations()
    store.apply_patch(repair_patch(store.snapshot()))
    assert store.snapshot()["tasks"][verification]["retired"]
    submit(store, "Q1", {"demand": 8})
    submit(store, "Q2", {"allocation": 8, "residual": 0})
    assert all(i["status"] == "resolved" for i in research_findings(store.snapshot()))
    assert store.snapshot()["assumptions"]["A1"]["status"] == "active"


class ClosedLoopModel(FakeModel):
    def with_structured_output(self, schema, **kwargs):
        outer = self
        class Bound:
            async def ainvoke(self, messages):
                if schema is ProblemAnalysis:
                    return problem()
                from ai_system.research.planner import EvidenceDecision
                if schema is EvidenceDecision:
                    context = json.loads(messages[-1].content)
                    from test_paper_graph import controller_reply
                    value=controller_reply(context)
                    if value['action']=='research' and context['assumptions'].get('A1',{}).get('refuted'):
                        value['changes']=[TaskChange(task_id='Q1',objective=next(t['objective'] for t in context['tasks'] if t['task_id']=='Q1'),
                            method_instruction='Use median demand').model_dump()]
                        value['assumptions']=[AssumptionChange(assumption_id='A1',base_revision=1,statement='Median demand is representative',
                            task_ids=['Q1','Q2'],checks=problem().assumptions[0].checks).model_dump()]
                    return EvidenceDecision.model_validate(value)
                if schema is ConsistencyReview:
                    return ConsistencyReview(conflicts=[])
                if schema is ClaimAudit:
                    return ClaimAudit(issues=[])
                if schema is ResultReview:
                    return ResultReview()
                return await FakeModel.with_structured_output(outer, schema, **kwargs).ainvoke(messages)
        return Bound()

    def _solver_step(self, task, messages):
        sid = re.search(r"# 子问题 (Q[0-9]+)", task)[1]
        contract, _ = json.JSONDecoder().raw_decode(task.split("# 研究契约", 1)[1].split("\n", 1)[1])
        results = [m for m in messages if isinstance(m, ToolMessage)]
        corrected = any(a["revision"] == 2 for a in contract["assumptions"])
        if not results:
            if sid == "Q1":
                code = "import statistics\nsamples = [8, 8, 20]\ndemand = " + ("statistics.median(samples)" if corrected else "statistics.mean(samples)") + "\nprint('demand', demand)"
            elif sid == "Q2":
                code = "import runpy\nsource = runpy.run_path('code/Q1_solution.py')\nallocation = source['demand']\nresidual = abs(allocation - 8)\nprint('allocation', allocation)\nprint('residual', residual)"
            else:
                code = "independent = sum([1, 2])\nprint('independent', independent)"
            name, args = "run_python", {"code": code}
        elif len(results) == 1:
            run_id = re.search(r"\[run_id: (\S+)\]", results[-1].content)[1]
            names = ["demand"] if sid == "Q1" else ["allocation", "residual"] if sid == "Q2" else ["independent"]
            name, args = "record_results", {"items": [{"name": metric, "value": re.search(rf"^{metric} (\S+)", results[-1].content, re.M)[1], "run_id": run_id} for metric in names]}
        else:
            refs = re.findall(r"\{\{R:(R[0-9]+)\}\}", results[-1].content)
            name, args = "SolveReport", {"summary": "已完成独立数据计算，结果为 {{R:" + refs[0] + "}}", "approach": "robust estimate",
                "key_findings": [{"statement": "当前登记结果为 {{R:" + refs[0] + "}}", "strength": "显著"}],
                "requirement_evidence": [{"requirement_id": r["id"], "evidence_refs": refs} for r in contract["requirements"]]}
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{sid}_{len(results)}"}])


def test_real_graph_automatically_repairs_upstream_and_downstream_and_delivers(tmp_path, monkeypatch):
    from ai_system.graph import llm_utils
    monkeypatch.setattr(llm_utils, "RETRY_DELAY", 0)
    agent = PaperGraphAgent(ClosedLoopModel(loose_number_once=False), workspace_dir=str(tmp_path), judge=FixedJudge(),
                           explore_top_k=1, max_review_rounds=0)
    output = asyncio.run(agent.run(TASK))
    assert "paper.md" in output
    state = ResearchStore(tmp_path, agent._load_thread()).snapshot()
    assert state['planning_calls']>4  # Initial actions, counterevidence repair and result-based convergence share one controller.
    assert [sum(a["task_id"] == key for a in state["attempts"].values()) for key in ("Q1", "Q2", "Q3")] == [2, 2, 1]
    assert state["assumptions"]["A1"]["revision"] == 2
    assert state["assumption_history"][0]["status"] == "refuted"
    assert all(i["status"] == "resolved" for i in research_findings(state))
    active = ResearchStore(tmp_path, agent._load_thread()).registry_payload()["results"]
    assert next(r["value"] for r in active if r["name"] == "demand") == "8"
    assert "12" not in (tmp_path / "paper.md").read_text()
    assert state["sections"]["solve:Q1"]["context_hash"]


class ReviewLoopModel(ClosedLoopModel):
    reviewed: bool = False
    counterexample: bool = True
    detection_phase: str = "reconcile"

    def with_structured_output(self, schema, **kwargs):
        base = super().with_structured_output(schema, **kwargs)
        owner = self
        class Bound:
            async def ainvoke(self, messages):
                detector = ConsistencyReview if owner.detection_phase == "reconcile" else ClaimAudit
                if schema is detector and not owner.reviewed:
                    from ai_system.graph.state import Conflict, ClaimIssue
                    owner.reviewed = True
                    refs = re.findall(r"⟨(R[0-9]+)⟩", messages[-1].content)
                    issue = IssueReport(task_id="Q1", category="assumption", assumption_id="A1", check_ids=["representative"],
                        message="上游估计与校核可能共享错误前提，请独立复算", evidence_refs=refs[:1])
                    if schema is ClaimAudit:
                        return ClaimAudit(issues=[ClaimIssue(section="evaluation", problem="validation may be circular", research_issue=issue)])
                    return ConsistencyReview(conflicts=[Conflict(claims=["C1"], issue="validation may be circular",
                        resolution="independent validation needed", research_issue=issue)])
                return await base.ainvoke(messages)
        return Bound()

    def _solver_step(self, task, messages):
        sid = re.search(r"# 子问题 (Q[0-9]+)", task)[1]
        contract, _ = json.JSONDecoder().raw_decode(task.split("# 研究契约", 1)[1].split("\n", 1)[1])
        results = [m for m in messages if isinstance(m, ToolMessage)]
        if not contract["requirements"]:  # An independent investigation uses the frozen check, not a model verdict.
            if not results:
                name, args = "run_python", {"code": "import statistics, runpy\nsource = runpy.run_path('code/Q1_solution.py')\nresidual = abs(source['demand'] - statistics.median([8, 8, 20]))\nprint('residual', residual)"}
            elif len(results) == 1:
                name, args = "record_results", {"items": [{"name": "residual", "value": re.search(r"^residual (\S+)", results[0].content, re.M)[1],
                                                           "run_id": re.search(r"\[run_id: (\S+)\]", results[0].content)[1]}]}
            else:
                ref = re.findall(r"\{\{R:(R[0-9]+)\}\}", results[-1].content)[0]
                name, args = "SolveReport", {"summary": "独立核验已完成，偏差为 {{R:" + ref + "}}", "approach": "independent validation",
                    "key_findings": [{"statement": "独立偏差 {{R:" + ref + "}}", "strength": "显著"}]}
            return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"verification_{len(results)}"}])
        reply = super()._solver_step(task, messages)
        if not results:
            code = reply.tool_calls[0]["args"]["code"]
            if sid == "Q1" and not self.counterexample:
                code = code.replace("statistics.mean", "statistics.median")
            if sid == "Q2":
                # A realistic bug: the original validation reuses its estimator's own premise.
                code = code.replace("allocation = source['demand']", "allocation = min(10, source['demand'])").replace("residual = abs(allocation - 8)", "residual = 0")
            reply.tool_calls[0]["args"]["code"] = code
        return reply


@pytest.mark.parametrize("counterexample", [True, False])
@pytest.mark.parametrize("detection_phase", ["reconcile", "sections"])
def test_consistency_suspicion_runs_independent_verification_then_replans_only_if_confirmed(tmp_path, monkeypatch, counterexample, detection_phase):
    from ai_system.graph import llm_utils
    monkeypatch.setattr(llm_utils, "RETRY_DELAY", 0)
    agent = PaperGraphAgent(ReviewLoopModel(counterexample=counterexample, detection_phase=detection_phase, loose_number_once=False), workspace_dir=str(tmp_path),
                           judge=FixedJudge(), explore_top_k=1, max_review_rounds=0)
    assert "paper.md" in asyncio.run(agent.run(TASK))
    store = ResearchStore(tmp_path, agent._load_thread())
    state = store.snapshot()
    investigations = [t for t in state["tasks"].values() if t["kind"] == "verify"]
    assert len(investigations) == 1 and investigations[0]["retired"]
    assert state["planning_calls"] == (3 if counterexample else 1)  # Initial deliver, confirmed repair, then a new deliver.
    assert sum(a["task_id"] == "Q1" for a in state["attempts"].values()) == (2 if counterexample else 1)
    assert sum(a["task_id"] == "Q3" for a in state["attempts"].values()) == 1
    assert research_findings(state)[0]["status"] == ("resolved" if counterexample else "dismissed")
    assert store.delivery_ready(state)
    from services.chat_services.research_session import research_snapshot
    snapshot = research_snapshot(tmp_path, "work", "completed")
    assert snapshot.schema_version == 3 and "paper.md" in snapshot.artifacts
    assert all(r["status"] == "accepted" for r in snapshot.requirements)
    historical = [r for r in snapshot.issue_evidence if r.subproblem_id == investigations[0]["task_id"]]
    assert len(historical) == 1 and historical[0].value == ("4" if counterexample else "0")
    run = next(r for r in snapshot.runs if r.id == historical[0].run_id)
    assert run.input_path and run.stdout_path
    assert all(r.id != historical[0].id for r in snapshot.results)
    if not counterexample and detection_phase == "reconcile":
        assert not json.loads((tmp_path / ".system/consistency.json").read_text())["conflicts"]
