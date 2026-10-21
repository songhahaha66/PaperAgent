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
from ai_system.research.models import AssumptionDraft, AssumptionProposal, DomainCheck, IssueReport, ResearchConflict, ResultSelector, ReviewRecord, WorkerResult
from ai_system.research.store import ResearchStore
from ai_system.research.validation import report_fingerprint
from test_paper_graph import FixedJudge, _candidate
from test_research_p2 import TASK, store_for, submit, resume_saved_contract




def draft():
    return AssumptionDraft(key="representative", statement="所用样本对当前任务具有代表性", checks=[
        DomainCheck(id="adopted_finite", description="估计值必须为有限数", left=ResultSelector(task_id="Q1", name="demand"), operator="finite")])


def run_agent(tmp_path, monkeypatch, model, **options):
    from ai_system.graph import llm_utils
    agent = PaperGraphAgent(model, workspace_dir=str(tmp_path), judge=FixedJudge(), explore_top_k=1, n_candidates=2,
                            max_review_rounds=1, **options)
    asyncio.run(resume_saved_contract(agent))
    store = ResearchStore(tmp_path, agent._load_thread())
    return agent, store
















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
