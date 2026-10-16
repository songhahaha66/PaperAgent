"""Submit, receive concrete gaps, fill them using tools, and submit again."""
import asyncio
import json
import re
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph.context import GraphContext
from ai_system.graph.nodes.solve import _solve_loop, build_task, submission_schema
from test_paper_graph import FakeModel


def branch(sid="Q1", *, kind="compute", figure=False):
    return {
        "subproblem": {"id": sid, "title": "整数求和", "question": "计算整数和" + ("，绘制结果图" if figure else ""),
                       "kind": kind, "depends_on": [], "deliverables": []},
        "data_profile": "无需外部数据", "dependencies": {}, "analysis": {},
        "design": {"chosen": {"name": "直接求和", "idea": "执行求和", "formulation": "", "steps": ["计算并登记"],
                               "method_keys": [], "assumptions": [], "validation": "固定指标检查"}},
        "research_contract": {
            "requirements": [] if kind == "verify" else [{"id": "RQ1"}], "assumptions": [],
            "checks": [{"owner_kind": "requirement", "owner_id": "RQ1", "owner_revision": 1,
                        "check": {"id": "answer_finite", "description": "结果有限", "left": {"task_id": "Q1", "name": "answer"},
                                  "operator": "finite", "expected": None, "right": None, "tolerance": 0}}],
        },
    }


def seed(ctx, runs, sid="Q1", name="answer", value="6"):
    rid = ctx.fs.create_run()
    ctx.fs.save_run_input(rid, "print(sum([1, 2, 3]))")
    ctx.fs.save_run_stdout(rid, value)
    ctx.fs.finish_run(rid, "success")
    runs.append({"run_id": rid, "ok": True, "code": "print(sum([1, 2, 3]))"})
    return ctx.registry.record_value(sid, name, value, rid)


def report(ref, *, requirement="RQ1"):
    return {"summary": f"已完成整数求和，结果为 {{{{R:{ref}}}}}", "approach": "Python直接求和",
            "key_findings": [{"statement": f"求和结果为 {{{{R:{ref}}}}}", "strength": "显著"}],
            "requirement_evidence": [{"requirement_id": requirement, "evidence_refs": [ref]}] if requirement else []}


@pytest.mark.parametrize("field", ["summary", "sensitivity", "approach", "limitations"])
def test_references_in_all_report_fields_must_be_from_the_frozen_scope(tmp_path, field):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    runs = []
    own = seed(ctx, runs)
    other = seed(ctx, [], sid="Q9")
    payload = report(own.id)
    payload[field] = [f"额外证据 {{{{R:{other.id}}}}}"] if field == "limitations" else f"额外证据 {{{{R:{other.id}}}}}"
    with pytest.raises(ValidationError, match="不是本子问题登记的结果"):
        submission_schema(ctx, branch(), runs).model_validate(payload)


@pytest.mark.parametrize("failure", ["missing_metric", "bad_mapping", "unknown_requirement", "changed_output", "failed_run", "other_attempt"])
def test_submission_rejects_missing_contract_evidence_and_invalid_execution_sources(tmp_path, failure):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    runs = []
    own = seed(ctx, runs, name="wrong_name" if failure == "missing_metric" else "answer")
    payload = report(own.id)
    if failure == "bad_mapping":
        payload["requirement_evidence"][0]["evidence_refs"] = ["R999"]
    if failure == "unknown_requirement":
        payload["requirement_evidence"][0]["requirement_id"] = "RQ999"
    if failure == "changed_output":
        ctx.fs.save_run_stdout(own.run_id, "no result")
    if failure == "failed_run":
        ctx.fs.finish_run(own.run_id, "error")
    if failure == "other_attempt":
        runs.clear()
    with pytest.raises(ValidationError) as error:
        submission_schema(ctx, branch(), runs).model_validate(payload)
    assert {"missing_metric": "answer_finite", "bad_mapping": "RQ1", "unknown_requirement": "RQ999",
            "changed_output": "来源无效", "failed_run": "执行失败", "other_attempt": "本次任务"}[failure] in str(error.value)


def test_check_threshold_failure_is_not_hidden_by_the_submission_gate(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    runs = []
    own = seed(ctx, runs)
    task = branch()
    task["research_contract"]["checks"][0]["check"].update(operator="le", expected=5)
    # A truthful counterexample is a valid report; the central acceptance layer
    # will reject the threshold and replan, rather than asking for a nicer number.
    assert submission_schema(ctx, task, runs).model_validate(report(own.id)).summary


def test_countercheck_uses_its_own_metric_and_allows_frozen_dependency_references(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    predecessor = seed(ctx, [], sid="Q1")
    runs = []
    own = seed(ctx, runs, sid="Q2")
    task = branch("Q2", kind="verify")
    task["subproblem"]["depends_on"] = ["Q1"]
    payload = report(own.id, requirement=None)
    payload["sensitivity"] = f"与冻结输入 {{{{R:{predecessor.id}}}}} 比较"
    assert submission_schema(ctx, task, runs).model_validate(payload).summary


@pytest.mark.parametrize("failure", ["missing", "deleted"])
def test_required_figure_must_be_registered_and_its_file_must_exist(tmp_path, failure):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    runs = []
    own = seed(ctx, runs)
    if failure == "deleted":
        artifact = ctx.fs.abs(f"runs/{own.run_id}/artifacts/figure_1.png")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"figure fixture")
        ctx.fs.register_run_artifact(own.run_id, "figure_1.png")
        figure = ctx.registry.record_figure("Q1", own.run_id, "figure_1.png", "结果图")
        ctx.fs.abs(figure.path).unlink()
    with pytest.raises(ValidationError, match="还没有登记任何图" if failure == "missing" else "图片文件不存在"):
        submission_schema(ctx, branch(figure=True), runs).model_validate(report(own.id))


def test_parallel_submission_schemas_do_not_share_task_context(tmp_path):
    first = GraphContext.create(str(tmp_path / "first"), planner_llm=FakeModel())
    second = GraphContext.create(str(tmp_path / "second"), planner_llm=FakeModel())
    first_runs, second_runs = [], []
    a, b = seed(first, first_runs), seed(second, second_runs)
    second_task = branch()
    second_task["research_contract"]["checks"][0]["check"]["left"]["name"] = "other_metric"
    first_schema = submission_schema(first, branch(), first_runs)
    second_schema = submission_schema(second, second_task, second_runs)
    assert first_schema.model_validate(report(a.id)).summary
    with pytest.raises(ValidationError, match="other_metric"):
        second_schema.model_validate(report(b.id))
    assert first_schema.model_validate(report(a.id)).summary


class FillGapsModel(FakeModel):
    mode: str = "missing_registration"
    submissions: int = 0
    feedback: list[str] = []

    def _solver_step(self, task, messages):
        tools = [m for m in messages if isinstance(m, ToolMessage)]
        errors = [m for m in tools if m.name == "SolveReport" and "报告未提交" in str(m.content)]
        if not tools:
            name, args = "run_python", {"code": "answer = sum([1, 2, 3])\nprint('answer', answer)\nprint('unrelated', 7)"}
        elif not errors and self.mode not in {"missing_registration", "stuck"} and not any(m.name == "record_results" for m in tools):
            run_id = re.search(r"\[run_id: (\S+)\]", tools[0].content)[1]
            name, args = "record_results", {"items": [{"name": "wrong_name" if self.mode == "missing_metric" else "answer",
                                                      "value": "6", "run_id": run_id}]}
        elif errors and self.mode != "stuck" and not any(m.name == "record_results" for m in tools[tools.index(errors[-1]) + 1:]) and self.mode != "bad_mapping":
            self.feedback.append(str(errors[-1].content))
            run_id = re.search(r"\[run_id: (\S+)\]", tools[0].content)[1]
            name, args = "record_results", {"items": [{"name": "answer", "value": "6", "run_id": run_id}]}
        else:
            self.submissions += 1
            refs = [ref for m in tools if m.name == "record_results" for ref in re.findall(r"\{\{R:(R\d+)\}\}", str(m.content))]
            name, args = "SolveReport", report(refs[-1] if refs else "R999")
            if self.mode == "bad_mapping" and not errors:
                args["requirement_evidence"] = []
            if errors:
                self.feedback.append(str(errors[-1].content))
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{len(messages)}"}])


@pytest.mark.parametrize("mode", ["missing_registration", "missing_metric", "bad_mapping"])
def test_real_tool_loop_rejects_report_fills_gaps_and_submits_without_rerunning_code(tmp_path, mode):
    model = FillGapsModel(mode=mode, loose_number_once=False)
    ctx = GraphContext.create(str(tmp_path), planner_llm=model, max_solver_steps=12, max_review_rounds=0)
    task = branch()
    runs = []

    async def go():
        try:
            return await _solve_loop(ctx, task, runs, [HumanMessage(content=build_task(task, ctx))])
        finally:
            await ctx.kernels.close()

    status, error, submitted, reviews = asyncio.run(go())
    assert status == "completed" and error is None and not reviews
    assert model.feedback and model.submissions == 2
    assert len(runs) == 1, "the same live kernel/output is reused to fill missing registration"
    own = next(r for r in ctx.registry.results.values() if r.name == "answer")
    assert submitted["requirement_evidence"] == [{"requirement_id": "RQ1", "evidence_refs": [own.id]}]
    transcript = json.loads((tmp_path / ".system/transcripts/Q1.json").read_text())
    assert any(m["type"] == "tool" and "报告未提交" in str(m["data"].get("content")) for m in transcript)


def test_repeated_invalid_submissions_exhaust_budget_without_a_fake_final_report(tmp_path):
    model = FillGapsModel(mode="stuck", loose_number_once=False)
    ctx = GraphContext.create(str(tmp_path), planner_llm=model, max_solver_steps=5, max_review_rounds=0)
    task, runs = branch(), []

    async def go():
        try:
            return await _solve_loop(ctx, task, runs, [HumanMessage(content=build_task(task, ctx))])
        finally:
            await ctx.kernels.close()

    status, error, submitted, _ = asyncio.run(go())
    assert status == "incomplete" and "步数" in error and "answer" in error
    assert submitted == {} and not ctx.registry.for_subproblem("Q1")
    assert len(runs) == 1 and model.submissions <= 5
    assert (tmp_path / ".system/transcripts/Q1.json").exists()
