"""Ground complete numerical vectors without weakening scalar acceptance."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.graph.context import GraphContext
from ai_system.graph.registry import GroundingError, ResultRegistry
from ai_system.modules.experiments.execution import build_tools, submission_schema
from ai_system.modules.experiments.review import submission_inventory_issues
from ai_system.research.validation import evaluate
from test_report_submission import branch, report


def context(tmp_path, output):
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    rid = ctx.fs.create_run()
    ctx.fs.save_run_stdout(rid, output)
    ctx.fs.finish_run(rid, "success")
    return ctx, rid


@pytest.mark.parametrize("output", ["fitted [440.0,442.5,-0.03,1000.0]", "fitted [440.  442.5\n -3.e-2  1.e+3]"])
def test_complete_series_is_grounded_persisted_and_rendered(tmp_path, output):
    ctx, rid = context(tmp_path, output)
    result = ctx.registry.record_value("Q1", "fitted", "[440.0, 442.5, -0.03, 1000.0]", rid, "份")
    assert result.kind == "series"
    loaded = ResultRegistry.load(ctx.fs)
    assert loaded.get(result.id) == result
    loaded.verify(result.value, rid)
    assert result.value in loaded.render("预测 {{R:" + result.id + "}}")


@pytest.mark.parametrize("output", ["[1,2,4]", "[3,2,1]", "[1,2,3,4]", "first 1\nsecond 2\nthird 3", "[1,...,3]", "[1,2,3x]"])
def test_series_cannot_collect_reorder_truncate_or_invent_values(tmp_path, output):
    ctx, rid = context(tmp_path, output)
    with pytest.raises(GroundingError):
        ctx.registry.record_value("Q1", "fitted", "[1,2,3]", rid)
    assert not ctx.registry.results


@pytest.mark.parametrize("value", ["[]", "[true,2]", "[1,NaN]", "[1,Infinity]", '[[1,2]]', '["a",2]'])
def test_only_finite_flat_numeric_series_can_be_registered(tmp_path, value):
    ctx, rid = context(tmp_path, value)
    with pytest.raises(GroundingError, match="一维有限数值"):
        ctx.registry.record_value("Q1", "fitted", value, rid)


def test_required_series_flows_through_tools_submission_and_domain_acceptance(tmp_path):
    ctx, rid = context(tmp_path, "fitted [1. 2. 3.]")
    runs = [{"run_id": rid, "ok": True}]
    task = branch()
    check = task["research_contract"]["checks"][0]["check"]
    check.update(operator="exists", expected=None)
    tools = {t.name: t for t in build_tools(ctx, "Q1", runs,
        submission_gaps=lambda: submission_inventory_issues(ctx, task, runs))}
    response = asyncio.run(tools["record_results"].ainvoke({"items": [{"name": "answer", "value": "[1,2,3]", "run_id": rid}]}))
    assert "必需登记指标已齐" in response and "登记失败" not in response
    result = next(iter(ctx.registry.results.values()))
    submission_schema(ctx, task, runs).model_validate(report(result.id))
    state = {"experiments": {"Q1": {"execution_id": "attempt", "acceptance": "pending", "validity": "current"}},
             "spec": {"requirements": [{"id": "RQ1", "revision": 1, "checks": [check]}]}, "assumptions": {}, "evidence": {}}
    payload = {"id": result.id, "subproblem": "Q1", "name": "answer", "value": result.value}
    assert evaluate(state, "Q1", [payload]).checks[0].outcome == "passed"
    check["operator"] = "finite"
    assert evaluate(state, "Q1", [payload]).checks[0].outcome != "passed"
