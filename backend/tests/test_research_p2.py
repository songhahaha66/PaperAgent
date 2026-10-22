"""P2 contracts, counterexamples, causal replanning and delivery gates."""

from helpers.contracts import TASK, problem, store_for, submit, resume_saved_contract

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
from helpers.graph import ProblemAnalysis, FakeModel, FixedJudge


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
