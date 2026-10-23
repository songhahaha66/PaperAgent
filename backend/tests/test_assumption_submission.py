"""Reject invalid premise selectors in the producing loop, before a central commit."""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.graph.context import GraphContext
from ai_system.modules.experiments.execution import submission_schema
from helpers.submissions import branch, report


pytestmark = pytest.mark.usefixtures("offline_kernel")

def draft(left="Q1", right=None):
    check = {"id": "trend_detection", "description": "趋势估计有限",
             "left": {"task_id": left, "name": "slope"}, "operator": "finite"}
    if right:
        check.update(operator="eq", right={"task_id": right, "name": "reference"})
    return {"key": "linear_trend", "statement": "短期趋势可以延续", "checks": [check]}


def contract():
    return {"task_ids": ["Q1", "Q2", "Q3"], "assumption_task_ids": ["Q1", "Q2"]}


@pytest.mark.parametrize("left,right,detail", [("q1", None, "q1"), ("Q3", None, "Q3"), ("Q1", "q2", "q2")])
def test_invalid_premise_selectors_are_repairable_report_errors(tmp_path, left, right, detail):
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    task = branch()
    task["research_contract"].update(contract())
    payload = report("R999")
    payload["assumptions"] = [draft(left, right)]
    schema = submission_schema(ctx, task, [])
    with pytest.raises(ValidationError) as error:
        schema.model_validate(payload)
    assert all(text in str(error.value) for text in ["linear_trend", "trend_detection", detail])
    payload["assumptions"] = [draft()]
    # A corrected new premise can be staged without pretending R999 is accepted evidence.
    assert schema.model_validate(payload).assumptions[0].checks[0].left.task_id == "Q1"
    assert not ctx.registry.results
