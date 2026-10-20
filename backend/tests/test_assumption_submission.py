"""Reject invalid premise selectors in the producing loop, before a central commit."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.graph import llm_utils
from ai_system.graph.context import GraphContext
from ai_system.modules.experiments.planning import propose
from ai_system.modules.experiments.execution import submission_schema
from ai_system.graph.state import CandidateSet
from ai_system.research.assumptions import unbound_assumptions, premise_definition
from ai_system.research.models import AssumptionDraft
from ai_system.research.control import task_dispatch as dispatch
from test_paper_graph import _analysis, _candidate
from test_report_submission import branch, report
from test_research_p2 import store_for
from types import SimpleNamespace


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


def test_duplicate_check_ids_rejected_but_real_cross_task_checks_preserved():
    value = draft("Q2", "Q3")
    assert unbound_assumptions("Q1", [value], contract())[0].checks[0].right.task_id == "Q3"
    value["checks"].append(dict(value["checks"][0]))
    with pytest.raises(ValueError, match="检查编号重复"):
        unbound_assumptions("Q1", [value], contract())


def test_design_retries_the_actual_bad_premise_before_any_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_utils, "RETRY_DELAY", 0)
    invalid = _candidate("线性趋势").model_dump()
    invalid["assumptions"] = [draft("q1")]
    class Model:
        calls = 0
        def with_structured_output(self, *args, **kwargs):
            return self
        async def ainvoke(self, messages):
            self.calls += 1
            assert "assumption_task_ids" in messages[1].content
            if self.calls == 1:
                return CandidateSet.model_validate({"candidates": [invalid]})
            assert "q1" in messages[-1].content and "trend_detection" in messages[-1].content
            assert json.loads(messages[-2].content) == CandidateSet.model_validate({"candidates": [invalid]}).model_dump()
            corrected = {**invalid, "assumptions": [draft()]}
            return AIMessage(content=json.dumps({"candidates": [corrected]}))
    model = Model()
    ctx = GraphContext.create(str(tmp_path), planner_llm=model)
    task = {**branch(), "analysis": _analysis().model_dump()}
    task["research_contract"].update(contract())
    result = asyncio.run(propose(ctx, task, ["regression"]))
    assert model.calls == 2
    assert result[0]["assumptions"][0]["checks"][0]["left"]["task_id"] == "Q1"
    assert not ctx.registry.results


@pytest.mark.parametrize("field", ["statement", "checks"])
def test_repeated_premise_feedback_identifies_key_and_repairs_without_new_staging(tmp_path, monkeypatch, field):
    monkeypatch.setattr(llm_utils, "RETRY_DELAY", 0)
    registered = draft()
    existing = {"id": "A:Q1:linear_trend", **premise_definition("Q1", AssumptionDraft.model_validate(registered))}
    repeated = draft()
    if field == "statement":
        repeated["statement"] = "重新改写同一趋势前提"
    else:
        repeated["checks"][0]["description"] = "改写原检查"
    candidate = _candidate("线性趋势").model_dump()
    candidate["assumptions"] = [repeated]
    class Model:
        calls = 0
        def with_structured_output(self, *args, **kwargs):
            return self
        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return CandidateSet.model_validate({"candidates": [candidate]})
            feedback = messages[-1].content
            assert "A:Q1:linear_trend" in feedback and field in feedback
            assert existing["statement"] in feedback and "删除这条重复声明" in feedback
            return AIMessage(content=json.dumps({"candidates": [{**candidate, "assumptions": []}]}))
    model = Model()
    ctx = GraphContext.create(str(tmp_path), planner_llm=model)
    task = {**branch(), "analysis": _analysis().model_dump()}
    task["research_contract"].update({**contract(), "assumptions": [existing]})
    result = asyncio.run(propose(ctx, task, ["regression"]))
    assert model.calls == 2 and result[0]["assumptions"] == []
    assert not ctx.registry.results


def test_dispatched_premise_scope_matches_central_dependency_scope(tmp_path):
    store = store_for(tmp_path)
    runtime = SimpleNamespace(context=SimpleNamespace(research=store))
    branches = dispatch({"analysis": _analysis().model_dump()}, runtime,['Q1','Q3'])
    scopes = {send.arg["subproblem"]["id"]: send.arg["research_contract"] for send in branches}
    assert scopes["Q1"]["assumption_task_ids"] == ["Q1", "Q2"]
    assert scopes["Q3"]["assumption_task_ids"] == ["Q3"]
    assert set(scopes["Q1"]["task_ids"]) == {"Q1", "Q2", "Q3"}


def test_default_design_executes_one_route_without_judging_or_probing(tmp_path):
    from ai_system.modules.experiments.planning import make_design
    from ai_system.modules.experiments.exploration import explore
    from test_paper_graph import FakeModel
    class UnexpectedJudge:
        async def ask(self, *args, **kwargs):
            raise AssertionError("a direct route must not launch candidate scoring")
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel(), judge=UnexpectedJudge())
    task = {**branch(), "analysis": _analysis().model_dump(), "design": None}
    async def go():
        design, _ = await make_design(ctx, task)
        assert len(design["candidates"]) == 1 and design["scores"] == [{"total": None}]
        assert await explore(ctx, {**task, "design": design}) is None
    asyncio.run(go())
    assert ctx.n_candidates == ctx.explore_top_k == 1
