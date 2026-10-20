"""Local audit findings and newly adopted premises share committed research state."""

from ai_system.research.domain import findings as research_findings
import asyncio
import json
import re
import sys
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph.agent import PaperGraphAgent
from ai_system.graph.state import CandidateSet, ResultReview
from ai_system.research.assumptions import assumption_id
from ai_system.research.models import (
    AssumptionChange, AssumptionDraft, AssumptionProposal, DomainCheck, IssueReport,
    PlanPatch, ResearchConflict, ResultSelector, ReviewRecord, TaskChange, WorkerResult,
)
from ai_system.research.store import ResearchStore
from ai_system.research.validation import report_fingerprint
from test_paper_graph import FixedJudge, _candidate
from test_research_p2 import ClosedLoopModel, ReviewLoopModel, TASK, store_for, submit, assert_countercheck_decisions_are_evidenced_and_reusable


class AuditModel(ClosedLoopModel):
    mode: str = "clear"
    review_calls: int = 0
    draft_source: str = ""

    def with_structured_output(self, schema, **kwargs):
        base = super().with_structured_output(schema, **kwargs)
        owner = self
        class Bound:
            async def ainvoke(self, messages):
                prompt = messages[-1].content
                if schema is ResultReview:
                    if "# 子问题 Q1：" not in prompt:
                        return ResultReview()
                    owner.review_calls += 1
                    if owner.mode == "service":
                        raise RuntimeError("offline local audit failure")
                    if owner.mode == "repair" and "samples = [8, 8, 8]" in prompt:
                        return ResultReview()
                    if owner.mode != "clear":
                        refs = re.findall(r"\{\{R:(R[0-9]+)\}\}", prompt.split("# 求解报告\n", 1)[1].split("# 登记结果", 1)[0])
                        return ResultReview(issues=[IssueReport(task_id="Q1", category="data",
                            message="只用单个样本，需要使用全部三个样本重新估计", evidence_refs=refs[:1])])
                    return ResultReview()
                if schema is CandidateSet and owner.draft_source == "design" and "子问题\nQ1 " in prompt:
                    discarded, selected = _candidate("方案甲"), _candidate("方案乙")
                    discarded.assumptions = [AssumptionDraft(key="discarded", statement="未采用路线的前提")]
                    selected.assumptions = [draft()]
                    return CandidateSet(candidates=[discarded, selected])
                return await base.ainvoke(messages)
        return Bound()

    def _solver_step(self, task, messages):
        sid = re.search(r"# 子问题 (Q[0-9]+)", task)[1]
        feedback = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage) and "评审老师审查" in m.content]
        active = messages[feedback[-1]:] if feedback else messages
        reply = super()._solver_step(task, active)
        if sid == "Q1":
            if reply.tool_calls[0]["name"] == "run_python":
                code = reply.tool_calls[0]["args"]["code"]
                reply.tool_calls[0]["args"]["code"] = code.replace("samples = [8, 8, 20]", "samples = [8, 8, 8]" if feedback and self.mode == "repair" else "samples = [8]")
            if reply.tool_calls[0]["name"] == "SolveReport" and self.draft_source == "solve":
                reply.tool_calls[0]["args"]["assumptions"] = [draft().model_dump()]
        return reply


def draft():
    return AssumptionDraft(key="representative", statement="所用样本对当前任务具有代表性", checks=[
        DomainCheck(id="adopted_finite", description="估计值必须为有限数", left=ResultSelector(task_id="Q1", name="demand"), operator="finite")])


def run_agent(tmp_path, monkeypatch, model, **options):
    from ai_system.graph import llm_utils
    monkeypatch.setattr(llm_utils, "RETRY_DELAY", 0)
    agent = PaperGraphAgent(model, workspace_dir=str(tmp_path), judge=FixedJudge(), explore_top_k=1, n_candidates=2,
                            max_review_rounds=1, **options)
    asyncio.run(agent.run(TASK))
    store = ResearchStore(tmp_path, agent._load_thread())
    return agent, store


def test_unresolved_local_review_blocks_delivery_without_unrelated_check_dismissing_it(tmp_path, monkeypatch):
    events=[]
    class Stream:
        async def send_json_block(self,kind,content):events.append((kind,content))
    agent, store = run_agent(tmp_path, monkeypatch, AuditModel(mode="stuck", loose_number_once=False),stream_manager=Stream())
    state = store.snapshot()
    findings = [i for i in research_findings(state) if i["source"] == "local_review"]
    assert findings and all(i["status"] == "inconclusive" for i in findings)
    assert state["outcome"] == "blocked" and not store.delivery_ready(state)
    assert not (tmp_path / "paper.md").exists()
    assert agent.last_result["research_blocked"]
    assert len(state["tasks"]) == 3, "a generic numeric check cannot dismiss a data-scope finding"
    assert not any(kind=='paper_solved' and content.startswith('Q1 ') for kind,content in events)
    with pytest.raises(ResearchConflict):
        store.publish_paper("ignore reviewer")


def test_successful_local_repair_gets_fresh_review_and_closes_recorded_findings(tmp_path, monkeypatch):
    model = AuditModel(mode="repair", loose_number_once=False)
    _, store = run_agent(tmp_path, monkeypatch, model)
    state = store.snapshot()
    assert store.delivery_ready(state) and (tmp_path / "paper.md").exists()
    assert model.review_calls == 2
    findings = [i for i in research_findings(state) if i["source"] == "local_review"]
    assert findings and all(i["status"] == "resolved" and i["resolution_attempt"] for i in findings)
    report = state["tasks"]["Q1"]["report"]
    assert [bool(r["issues"]) for r in report["reviews"]] == [True, False]
    assert report["reviews"][-1]["report_hash"] == report_fingerprint(report)
    assert "samples = [8, 8, 8]" in (tmp_path / "code/Q1_solution.py").read_text()
    from services.chat_services.research_session import research_snapshot
    snapshot = research_snapshot(tmp_path, "work", "completed")
    old_refs = {ref for i in findings for ref in i["evidence_refs"]}
    assert old_refs and old_refs.isdisjoint({r.id for r in snapshot.results})
    assert old_refs <= {r.id for r in snapshot.issue_evidence}
    assert all(not state["evidence"][ref]["active"] for ref in old_refs)


@pytest.mark.parametrize("mode", ["service", "stuck"])
def test_resume_rechecks_local_findings_instead_of_clearing_them_at_recovery(tmp_path, monkeypatch, mode):
    _, store = run_agent(tmp_path, monkeypatch, AuditModel(mode=mode, loose_number_once=False))
    original = store.snapshot()
    unrelated = original["tasks"]["Q3"]["active_attempt"]
    assert not store.delivery_ready(original)
    store.recover()
    assert any(i["source"] == "local_review" and i["status"] not in {"resolved", "dismissed"} for i in research_findings(store.snapshot()))
    agent = PaperGraphAgent(AuditModel(mode="clear", loose_number_once=False), workspace_dir=str(tmp_path), judge=FixedJudge(), explore_top_k=1, max_review_rounds=1)
    asyncio.run(agent.resume_failed())
    state = store.snapshot()
    assert store.delivery_ready(state) and (tmp_path / "paper.md").exists()
    assert state["tasks"]["Q3"]["active_attempt"] == unrelated
    assert all(i["status"] in {"resolved", "dismissed"} for i in research_findings(state))
    retry = state["attempts"][state["tasks"]["Q1"]["active_attempt"]]
    assert retry["review_issue_ids"], "the new audit is bound to the previously open findings"


def test_resume_routes_uncheckable_global_finding_to_owner_and_fresh_audit(tmp_path, monkeypatch):
    _, store = run_agent(tmp_path, monkeypatch, AuditModel(loose_number_once=False))
    original = store.snapshot()
    finding = IssueReport(task_id="Q1", category="model", section="analysis", message="核对逐日序列，不能用单日值与全周平均比较")
    [issue] = store.report_issues([finding], "sections")
    store.schedule_investigations()
    assert research_findings(store.snapshot())[-1]["status"] == "inconclusive"
    store.recover()
    assert research_findings(store.snapshot())[-1]["status"] == "inconclusive", "recovery itself is not a resolution"

    class RecheckModel(AuditModel):
        solver_saw_finding: bool = False
        reviewer_saw_finding: bool = False

        def _solver_step(self, task, messages):
            self.solver_saw_finding |= finding.message in task
            return super()._solver_step(task, messages)

        def with_structured_output(self, schema, **kwargs):
            bound = super().with_structured_output(schema, **kwargs)
            owner = self
            class Bound:
                async def ainvoke(self, messages):
                    if schema is ResultReview:
                        owner.reviewer_saw_finding |= finding.message in messages[-1].content
                    return await bound.ainvoke(messages)
            return Bound()

    model = RecheckModel(loose_number_once=False)
    agent = PaperGraphAgent(model, workspace_dir=str(tmp_path), judge=FixedJudge(), explore_top_k=1, max_review_rounds=1)
    asyncio.run(agent.resume_failed())
    state = store.snapshot()
    assert model.solver_saw_finding and model.reviewer_saw_finding
    assert store.delivery_ready(state)
    assert state["tasks"]["Q3"]["active_attempt"] == original["tasks"]["Q3"]["active_attempt"]
    resolved = next(i for i in research_findings(state) if i["id"] == issue["id"])
    assert resolved["source"] == "sections" and resolved["status"] == "resolved"
    attempt = state["attempts"][resolved["resolution_attempt"]]
    assert issue["id"] in attempt["review_issue_ids"]
    report = state["tasks"]["Q1"]["report"]
    assert report["reviews"][-1]["report_hash"] == report_fingerprint(report)


@pytest.mark.parametrize("audit", ["missing", "stale", "still_open"])
def test_global_finding_cannot_close_without_fresh_clear_owner_review(tmp_path, audit):
    store = store_for(tmp_path)
    submit(store, "Q1", {"demand": 8})
    finding = IssueReport(task_id="Q1", category="model", message="qualitative concern")
    [issue] = store.report_issues([finding], "sections")
    store.schedule_investigations()
    store.recover()
    order = store.claim("Q1", review_required=True)
    assert order.review_issue_ids == [issue["id"]]
    report = {"status": "completed", "summary": "computed", "requirement_evidence": [{"requirement_id": "RQ1", "evidence_refs": ["R99"]}]}
    reviews = [] if audit == "missing" else [ReviewRecord(
        report_hash="old" if audit == "stale" else report_fingerprint(report),
        issues=[finding] if audit == "still_open" else [])]
    store.commit(WorkerResult(order=order, execution_status="succeeded", acceptance="accepted", report=report,
        results=[{"id": "R99", "subproblem": "Q1", "name": "demand", "value": "8", "run_id": "run_test"}], reviews=reviews))
    assert next(i for i in research_findings(store.snapshot()) if i["id"] == issue["id"])["status"] == "inconclusive"
    assert not store.delivery_ready(store.snapshot())


def test_global_finding_with_executable_check_keeps_independent_investigation(tmp_path):
    store = store_for(tmp_path)
    submit(store, "Q1", {"demand": 8})
    submit(store, "Q2", {"allocation": 8, "residual": 0})
    [issue] = store.report_issues([IssueReport(task_id="Q2", category="model", message="check capacity", check_ids=["capacity"])], "sections")
    [verification] = store.schedule_investigations()
    store.recover()
    assert store.snapshot()["tasks"]["Q2"]["acceptance"] == "accepted"
    assert issue["id"] not in store.claim(verification, review_required=True).review_issue_ids


@pytest.mark.parametrize("source", ["design", "solve"])
def test_adopted_premises_are_versioned_before_publishing_and_frozen_on_rerun(tmp_path, monkeypatch, source):
    events=[]
    published=[]
    requested_designs=[]
    if source=='design':
        # This case explicitly requests the optional design capability. Ordinary
        # execution remains direct; only the selected candidate's premise enters
        # the existing prepare/commit/rerun protocol.
        from ai_system.modules.experiments import execution
        from ai_system.modules.registry import invoke
        solve=execution.solve
        async def with_requested_design(branch,runtime):
            if branch['subproblem']['id']=='Q1' and branch.get('design') is None:
                design,_=await invoke(runtime.context,'experiments.design',branch=branch)
                requested_designs.append(branch['subproblem']['id'])
                branch={**branch,'design':design}
            return await solve(branch,runtime)
        monkeypatch.setattr(execution,'solve',with_requested_design)
    class Stream:
        async def send_json_block(self,kind,content):
            if kind=='file_changed' and content.startswith('outputs/'):
                published.append((content,(tmp_path/content).is_file()))
            if kind=='paper_solved' and content.startswith('Q1 '):
                thread=json.loads((tmp_path/'.system/graph_thread.json').read_text())['thread_id']
                current=ResearchStore(tmp_path,thread,read_only=True).snapshot()
                task=current['tasks']['Q1']
                assert task['acceptance']=='accepted' and task['validity']=='current'
                assert current['attempts'][task['active_attempt']]['input_refs']['assumption:A:Q1:representative']=='1'
                events.append(content)
    _, store = run_agent(tmp_path, monkeypatch, AuditModel(draft_source=source, loose_number_once=False),stream_manager=Stream())
    state = store.snapshot()
    aid = assumption_id("Q1", "representative")
    premise = state["assumptions"][aid]
    assert premise["revision"] == 1 and premise["source"] == source
    assert requested_designs==(['Q1'] if source=='design' else [])
    assert premise["task_ids"] == ["Q1", "Q2"]
    assert assumption_id("Q1", "discarded") not in state["assumptions"]
    attempts = [a for a in state["attempts"].values() if a["task_id"] == "Q1"]
    assert len(attempts) == 2 and attempts[0]["commit"]["published"] is False
    assert attempts[0]["introduced_assumptions"] == [aid]
    active = state["attempts"][state["tasks"]["Q1"]["active_attempt"]]
    assert active["input_refs"]["assumption:" + aid] == "1"
    assert all(c["assumption_refs"][aid] == 1 for c in state["claims"].values() if c["producer_attempt"] == active["attempt_id"])
    assert all(e["producer_attempt"] != attempts[0]["attempt_id"] for e in state["evidence"].values() if e["payload"]["id"] in {r["id"] for r in store.registry_payload()["results"]})
    assert store.delivery_ready(state)
    assert len(events)==1 and '已通过验收' in events[0]
    assert all(exists for _,exists in published), 'published file events must point to existing root-workspace artifacts'


def test_review_contract_has_no_legacy_sound_or_must_fix_fields():
    with pytest.raises(ValueError):
        ResultReview.model_validate({"sound": False, "must_fix": ["unresolved"]})


def test_clear_local_audit_cannot_close_an_independent_solver_finding(tmp_path):
    store = store_for(tmp_path)
    report = {"status": "completed", "summary": "computed", "requirement_evidence": [{"requirement_id": "RQ1", "evidence_refs": ["R1"]}]}
    finding = IssueReport(task_id="Q1", category="model", message="unresolved solver finding")
    store.commit(WorkerResult(order=store.claim("Q1", review_required=True), execution_status="succeeded", acceptance="accepted", report=report,
        results=[{"id": "R1", "subproblem": "Q1", "name": "demand", "value": "8", "run_id": "run_test"}], issues=[finding],
        reviews=[ReviewRecord(report_hash=report_fingerprint(report), issues=[finding]), ReviewRecord(report_hash=report_fingerprint(report), issues=[])]))
    issues = research_findings(store.snapshot())
    assert next(i for i in issues if i["source"] == "local_review")["status"] == "resolved"
    assert next(i for i in issues if i["source"] == "worker")["status"] == "suspected"
    assert not store.delivery_ready(store.snapshot())


def test_downstream_rewrite_cannot_close_a_finding_about_its_frozen_upstream(tmp_path):
    store = store_for(tmp_path)
    _, upstream = submit(store, "Q1", {"demand": 8})
    report = {"status": "completed", "summary": "computed", "requirement_evidence": [{"requirement_id": "RQ2", "evidence_refs": ["R2"]}]}
    finding = IssueReport(task_id="Q1", category="data", message="上游数据范围需要核验", evidence_refs=[upstream[0]["id"]])
    store.commit(WorkerResult(order=store.claim("Q2", review_required=True), execution_status="succeeded", acceptance="accepted", report=report,
        results=[{"id": "R2", "subproblem": "Q2", "name": "allocation", "value": "8", "run_id": "run_test"},
                 {"id": "R3", "subproblem": "Q2", "name": "residual", "value": "0", "run_id": "run_test"}],
        reviews=[ReviewRecord(report_hash=report_fingerprint(report), issues=[finding]), ReviewRecord(report_hash=report_fingerprint(report), issues=[])]))
    [issue] = research_findings(store.snapshot())
    assert issue["task_id"] == "Q1" and issue["status"] == "suspected"
    assert not store.delivery_ready(store.snapshot())


@pytest.mark.parametrize("record_kind", ["missing", "stale", "open"])
def test_commit_requires_fresh_review_and_cannot_hide_open_findings(tmp_path, record_kind):
    store = store_for(tmp_path)
    order = store.claim("Q1", review_required=True)
    report = {"status": "completed", "summary": "computed", "requirement_evidence": [{"requirement_id": "RQ1", "evidence_refs": ["R1"]}]}
    record = ReviewRecord(report_hash="stale" if record_kind == "stale" else report_fingerprint(report), issues=[] if record_kind != "open" else [IssueReport(task_id="Q1", category="model", message="unresolved")])
    result = WorkerResult(order=order, execution_status="succeeded", acceptance="accepted", report=report,
        results=[{"id": "R1", "subproblem": "Q1", "name": "demand", "value": "8", "run_id": "run_test"}],
        reviews=[] if record_kind == "missing" else [record])
    store.commit(result)
    assert not store.delivery_ready(store.snapshot())
    assert any(i["source"] == "local_review" and i["blocking"] for i in research_findings(store.snapshot()))
    with pytest.raises(ResearchConflict, match="冻结输入"):
        store.commit(result.model_copy(update={"order": order.model_copy(update={"attempt_id": store.claim("Q3").attempt_id, "review_required": False})}))


def test_premise_registration_is_idempotent_stale_safe_and_keeps_unrelated_work(tmp_path):
    store = store_for(tmp_path)
    submit(store, "Q3", {"independent": 3})
    original = store.snapshot()["tasks"]["Q3"]["active_attempt"]
    order = store.claim("Q1")
    result = WorkerResult(order=order, execution_status="succeeded", acceptance="inconclusive", report={"status": "prepared"},
        assumptions=[AssumptionProposal(**draft().model_dump(), source="design")])
    receipt = store.commit(result)
    assert not receipt["published"] and store.commit(result) == receipt
    aid = assumption_id("Q1", "representative")
    assert store.snapshot()["assumptions"][aid]["revision"] == 1
    assert store.snapshot()["tasks"]["Q3"]["active_attempt"] == original
    before = store.snapshot()
    changed = store.claim("Q1")
    with pytest.raises(ResearchConflict, match="覆盖"):
        store.commit(WorkerResult(order=changed, execution_status="succeeded", acceptance="inconclusive", report={},
            assumptions=[AssumptionProposal(key="representative", statement="a different premise", source="solve")]))
    assert store.snapshot()["assumptions"] == before["assumptions"]
    store.apply_patch(PlanPatch(command_id="change", base_revision=before["revision"], reason="new strategy",
        changes=[TaskChange(task_id="Q1", objective="new strategy")]))
    stale = result.model_copy(update={"order": changed, "assumptions": [AssumptionProposal(key="stale", statement="obsolete", source="solve")]})
    assert not store.commit(stale)["published"]
    assert assumption_id("Q1", "stale") not in store.snapshot()["assumptions"]


def test_parallel_tasks_can_introduce_the_same_local_premise_and_check_keys(tmp_path):
    store = store_for(tmp_path)
    orders = [store.claim(key) for key in ("Q1", "Q3")]
    for order in orders:
        metric = "demand" if order.task_id == "Q1" else "independent"
        proposal = AssumptionProposal(key="representative", statement="task-local premise", source="design", checks=[
            DomainCheck(id="finite", description="finite estimate", left=ResultSelector(task_id=order.task_id, name=metric), operator="finite")])
        store.commit(WorkerResult(order=order, execution_status="succeeded", acceptance="inconclusive", report={}, assumptions=[proposal]))
    created = [a for a in store.snapshot()["assumptions"].values() if a["source"] == "design"]
    assert len(created) == 2 and len({a["checks"][0]["id"] for a in created}) == 2


def test_new_consumers_inherit_versioned_premises_and_refutation_recomputes_them(tmp_path):
    store = store_for(tmp_path)
    local = AssumptionDraft(key="representative", statement="sample representative", checks=[DomainCheck(id="scope_check",
        description="zero residual", left=ResultSelector(task_id="Q2", name="residual"), operator="le", expected=0)])
    store.commit(WorkerResult(order=store.claim("Q1"), execution_status="succeeded", acceptance="inconclusive", report={},
        assumptions=[AssumptionProposal(**local.model_dump(), source="solve")]))
    aid = assumption_id("Q1", local.key)
    state = store.snapshot()
    store.apply_patch(PlanPatch(command_id="add", base_revision=state["revision"], reason="additional work",
        changes=[TaskChange(action="add", task_id="Q4", objective="consume demand", dependencies=["Q1"], requirement_ids=["RQ1"])]))
    submit(store, "Q1", {"demand": 8})
    consumer = store.claim("Q4")
    assert consumer.input_refs["assumption:" + aid] == "1"
    submit(store, "Q2", {"allocation": 8, "residual": 4})
    state = store.snapshot()
    assert state["assumptions"][aid]["status"] == "refuted"
    roots = [AssumptionChange(assumption_id=key, base_revision=1, statement="revised premise", task_ids=a["task_ids"], checks=a["checks"])
        for key, a in state["assumptions"].items() if a["status"] == "refuted"]
    store.apply_patch(PlanPatch(command_id="repair", base_revision=state["revision"], reason="replace refuted premises",
        assumptions=roots, issue_ids=[i["id"] for i in research_findings(state) if i["status"] == "confirmed"]))
    assert store.snapshot()["assumptions"][aid]["revision"] == 2
    assert store.snapshot()["assumptions"][aid]["source"] == "solve"
    assert any(a["id"] == aid and a["revision"] == 1 and a["status"] == "refuted" for a in store.snapshot()["assumption_history"])
    assert not submit(store, "Q4", {"summary": 8}, order=consumer)[0]["published"]


class LocalCountercheckModel(ReviewLoopModel):
    reviewed: bool = True  # Findings originate in local auditing, not reconciliation.
    local_audits: int = 0

    def with_structured_output(self, schema, **kwargs):
        base = super().with_structured_output(schema, **kwargs)
        owner = self
        class Bound:
            async def ainvoke(self, messages):
                prompt = messages[-1].content
                if schema is ResultReview:
                    if "# 子问题 Q1：" not in prompt:
                        return ResultReview()
                    owner.local_audits += 1
                    if owner.local_audits > 2 and "demand = statistics.median(samples)" in prompt:
                        return ResultReview()
                    return ResultReview(issues=[IssueReport(task_id="Q1", category="assumption", assumption_id="A1",
                        check_ids=["representative"], message="需要独立核验估计前提")])
                return await base.ainvoke(messages)
        return Bound()


@pytest.mark.parametrize("counterexample", [True, False])
def test_local_findings_use_independent_counterchecks_and_only_replan_after_confirmation(tmp_path, monkeypatch, counterexample):
    agent, store = run_agent(tmp_path, monkeypatch, LocalCountercheckModel(counterexample=counterexample, loose_number_once=False))
    state = store.snapshot()
    [issue] = [i for i in research_findings(state) if i["source"] == "local_review"]
    assert issue["status"] == ("resolved" if counterexample else "dismissed")
    assert_countercheck_decisions_are_evidenced_and_reusable(agent,store,counterexample)
    assert sum(a["task_id"] == "Q3" for a in state["attempts"].values()) == 1
    assert len([t for t in state["tasks"].values() if t["kind"] == "verify"]) == 1
    assert store.delivery_ready(state) and (tmp_path / "paper.md").exists()
