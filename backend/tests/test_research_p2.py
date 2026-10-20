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
from ai_system.research.models import Assumption, DomainCheck, IssueReport, Requirement, ResultSelector, WorkerResult, ResearchConflict
from ai_system.research.store import ResearchStore
from ai_system.research.planner import ready_tasks
from ai_system.graph.agent import PaperGraphAgent
from ai_system.graph.state import Subproblem, ConsistencyReview, ClaimAudit, ResultReview
from test_paper_graph import ProblemAnalysis, FakeModel, FixedJudge

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
            {"requirement_id": rid, "evidence_refs": [results[0]["id"]]} for rid in store.snapshot()["experiments"][key]["requirement_ids"]]},
        results=results, issues=list(issues))
    return store.commit(result), results






def test_missing_metric_is_inconclusive_and_cannot_publish(tmp_path):
    store = store_for(tmp_path)
    submit(store, "Q1", {"demand": 8})
    submit(store, "Q2", {"allocation": 8})
    attempt = list(store.snapshot()["executions"].values())[-1]
    assert attempt["acceptance"] == "inconclusive"
    assert store.snapshot()["assumptions"]["A1"]["status"] == "active"
    with pytest.raises(ResearchConflict):
        store.publish_paper("pretend everything is valid")








def test_source_and_check_contracts_fail_closed(tmp_path):
    analysis = problem().model_dump()
    analysis["requirements"][0]["source_quote"] = "not in original"
    with pytest.raises(ValueError, match="摘录"):
        ResearchStore(tmp_path, "invalid").initialize(TASK, analysis)
    with pytest.raises(ValueError):
        DomainCheck(id="bad", description="bad", left=ResultSelector(task_id="Q1", name="x"), operator="le")
    # A source constraint can exist before a concrete experiment/check.
    requirement = Requirement(id="pending", statement="capacity", source_quote="capacity", kind="constraint")
    assert not requirement.task_ids and not requirement.checks


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










async def resume_saved_contract(agent):
    """Counterevidence fixtures import a saved full contract at the recovery boundary.

    New-run lightweight decisions are exercised separately; these cases retain
    their original assumption IDs to verify repair and independent counterchecks.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from ai_system.graph.build import compile_graph
    if agent._load_thread() is None:
        thread='saved-contract'
        ResearchStore(agent.workspace_dir,thread).initialize(TASK,problem().model_dump())
        agent._save_thread(thread,'paused')
        async with AsyncSqliteSaver.from_conn_string(str(Path(agent.workspace_dir)/'.system/graph.sqlite')) as saver:
            graph=compile_graph(saver)
            await graph.aupdate_state({'configurable':{'thread_id':thread}},
                                     {'task':TASK,'analysis':problem().model_dump()},as_node='solve_join')
    return await agent.resume_failed()
