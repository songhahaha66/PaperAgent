"""P2 contracts, counterexamples, causal replanning and delivery gates."""

from helpers.contracts import TASK, problem, submit

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.research.models import DomainCheck, ResultSelector
from ai_system.research.store import ResearchStore


pytestmark = pytest.mark.usefixtures("offline_kernel")

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
