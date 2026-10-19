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
from ai_system.modules.experiments.execution import _solve_loop, build_task, build_tools, submission_schema
from ai_system.graph.state import NegativeControl
from test_paper_graph import FakeModel


@pytest.mark.parametrize("chance", ["见RMSE行", float("nan"), float("inf")])
def test_control_baseline_requires_a_real_scalar_at_submission(chance):
    row = dict(metric="MAE", value="5", run_id="run_real", shuffled_value="8", shuffled_run_id="run_shuffle",
               chance=chance, higher_is_better=False)
    with pytest.raises(ValidationError, match="chance"):
        NegativeControl.model_validate(row)
    row["chance"] = 10.5
    assert NegativeControl.model_validate(row).chance == 10.5


def branch(sid="Q1", *, kind="compute", figure=False):
    return {
        "source": "计算 1、2、3 的整数和，并保留代码与结果。",
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


@pytest.mark.parametrize("figures", [False, True])
def test_repeating_execution_can_request_report_only_after_frozen_inventory_is_complete(tmp_path, figures):
    from langchain.agents.middleware import ModelRequest
    from langchain_core.messages import AIMessage,ToolMessage
    from ai_system.graph.compaction import StepBudgetMiddleware
    from ai_system.modules.experiments.review import submission_inventory_issues
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    task=branch();runs=[]
    messages=[HumanMessage(content='solve')]
    for i in range(3):
        messages.extend([AIMessage(content='',tool_calls=[{'id':str(i),'name':'run_python','args':{'code':"print('saved')"}}]),
                         ToolMessage(content=f'[run_id: run_{i}]\nsaved' +
                             (f'\n已保存图片: runs/run_{i}/artifacts/figure_{i+1}.png' if figures else ''),tool_call_id=str(i))])
    middleware=StepBudgetMiddleware(40,4,'submit',report_ready=lambda: not submission_inventory_issues(ctx,task,runs))
    seen=[]
    async def handler(request):seen.append(request);return None
    def invoke(history):asyncio.run(middleware.awrap_model_call(ModelRequest(model=None,messages=history,tools=['run_python']),handler))
    invoke(messages)
    assert seen[-1].tools==['run_python']  # Missing actual answer still allows computation.
    assert "登记证据仍未齐" in seen[-1].messages[-1].content
    assert "只补算缺项" in seen[-1].messages[-1].content
    seed(ctx,runs)
    invoke(messages)
    assert seen[-1].tools==[] and '必需登记证据已齐' in seen[-1].messages[-1].content
    changed=[*messages[:-1],messages[-1].model_copy(update={'content':'[run_id: run_2]\nchanged'})]
    invoke(changed)
    assert seen[-1].tools==['run_python']  # Identical code with changing results is not a stall.
    failed=[*messages[:-1],messages[-1].model_copy(update={'content':'[run_id: run_2]\nTraceback (most recent call last): failure'})]
    invoke(failed)
    assert seen[-1].tools==['run_python']


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


def test_submission_grounds_summary_and_equivalent_numeric_formatting(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    runs = []
    own = seed(ctx, runs)
    rid = own.run_id
    ctx.fs.save_run_stdout(rid, "6\n0.3\n50.0")
    limit = ctx.registry.record_value("Q1", "grid lower bound", "0.3", rid)
    pct = ctx.registry.record_value("Q1", "comparison threshold", "50.0", rid, "%")
    payload = report(own.id)
    payload["summary"] = "网格下限0.30，阈值50%。"
    accepted = submission_schema(ctx, branch(), runs).model_validate(payload)
    assert f"{{{{R:{limit.id}}}}}" in accepted.summary
    assert f"{{{{R:{pct.id}}}}}" in accepted.summary
    # A genuinely different value, ambiguous metric or incompatible unit must
    # still require the model to choose an explicit, grounded reference.
    assert "0.31" in ctx.registry.ground_statement("Q1", "下限 0.31")
    ctx.registry.record_value("Q1", "other bound", "0.30", rid)
    assert "0.300" in ctx.registry.ground_statement("Q1", "下限 0.300")
    assert "50.00" in ctx.registry.ground_statement("Q1", "下限 50.00")


def test_review_retains_original_computation_and_its_output_after_formatting_retries(tmp_path):
    from ai_system.modules.experiments.review import review_prompt
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    runs = []
    for index in range(10):
        rid = ctx.fs.create_run()
        code = "values = [1, 2, 3]\nanswer = sum(values)\nprint(answer)" if index == 0 else f"print('format retry {index}')"
        ctx.fs.save_run_input(rid, code)
        ctx.fs.save_run_stdout(rid, "actual_sum 6" if index == 0 else str(index))
        ctx.fs.finish_run(rid, "success")
        runs.append({"run_id": rid, "code": code, "ok": True})
    prompt = review_prompt(branch(), {}, ctx, runs)
    assert runs[0]["code"] in prompt and "actual_sum 6" in prompt
    assert runs[-1]["code"] in prompt
    assert "标量均值不能代替序列" in prompt and "超参数" in prompt


def test_optional_controls_do_not_bypass_an_explicit_frozen_experiment(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    runs = []
    own = seed(ctx, runs)
    task = branch()
    task["research_contract"]["checks"][0]["check"]["left"]["name"] = "required_shuffle_metric"
    payload = report(own.id)
    payload["controls"] = []
    with pytest.raises(ValidationError, match="required_shuffle_metric"):
        submission_schema(ctx, task, runs).model_validate(payload)


def test_registration_returns_current_inventory_without_waiting_for_a_failed_report(tmp_path):
    from ai_system.modules.experiments.review import submission_inventory_issues
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    task, runs = branch(), []
    tools = {tool.name: tool for tool in build_tools(ctx, "Q1", runs,
        submission_gaps=lambda: submission_inventory_issues(ctx, task, runs))}
    async def go():
        try:
            output = await tools["run_python"].ainvoke({"code": "print(sum([1, 2, 3]))"})
            assert "answer_finite" in output and "按契约原名登记" in output
            assert runs[0]["run_id"] in output
            item = {"name": "wrong_name", "value": "6", "run_id": runs[0]["run_id"]}
            missing = await tools["record_results"].ainvoke({"items": [item]})
            assert "answer_finite" in missing and "缺少唯一登记指标" in missing
            complete = await tools["record_results"].ainvoke({"items": [{**item, "name": "answer"}]})
            assert "必需登记指标已齐" in complete and "缺少唯一登记指标" not in complete
            assert len(runs) == 1
        finally:
            await ctx.kernels.close()
    asyncio.run(go())


def test_failed_execution_cannot_prompt_registration_of_nonexistent_results(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    runs = []
    tools = {tool.name: tool for tool in build_tools(ctx, "Q1", runs,
        submission_gaps=lambda: ["answer_finite 缺少登记结果"])}
    async def go():
        try:
            output = await tools["run_python"].ainvoke({"code": "raise ValueError('incomplete computation')"})
            assert "执行失败" in output and "本次run不能作为登记来源" in output
            assert "[提交检查]" not in output
            assert len(runs) == 1 and not runs[0]["ok"] and not ctx.registry.results
        finally:
            await ctx.kernels.close()
    asyncio.run(go())


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


class MixedReportModel(FakeModel):
    valid_mixed: bool = False
    deferred: bool = False
    def _solver_step(self, task, messages):
        tools = [m for m in messages if isinstance(m, ToolMessage)]
        refs = [ref for m in tools if m.name == "record_results" for ref in re.findall(r"\{\{R:(R\d+)\}\}", str(m.content))]
        if not tools:
            calls = [("run_python", {"code": "print(sum([1, 2, 3]))"})]
        elif not refs:
            run_id = re.search(r"\[run_id: (\S+)\]", tools[0].content)[1]
            calls = [("record_results", {"items": [{"name": "answer", "value": "6", "run_id": run_id}]})]
            if not self.valid_mixed:
                calls.append(("SolveReport", report("R999")))
        elif self.valid_mixed and sum(m.name == "run_python" for m in tools) == 1:
            calls = [("run_python", {"code": "print('independent audit', 6)"}), ("SolveReport", report(refs[-1]))]
        else:
            deferred = [m for m in messages if isinstance(m, AIMessage) and "本轮报告未提交" in str(m.content)]
            assert deferred, "mixed submissions must not terminate or bypass the producing loop"
            if not self.valid_mixed:
                assert "RQ1" in deferred[-1].content, "preserve concrete submission gaps for repair"
            self.deferred = True
            calls = [("SolveReport", report(refs[-1]))]
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{len(messages)}_{i}"}
                                                 for i, (name, args) in enumerate(calls)])


def test_compute_feedback_routes_stalled_solver_into_registration_before_report(tmp_path):
    class InventoryModel(FakeModel):
        def _solver_step(self, task, messages):
            tools = [m for m in messages if isinstance(m, ToolMessage)]
            recorded = [m for m in tools if m.name == "record_results"]
            outputs = [m for m in tools if m.name == "run_python"]
            if recorded:
                ref = re.search(r"\{\{R:(R\d+)\}\}", str(recorded[-1].content))[1]
                name, args = "SolveReport", report(ref)
            elif outputs and "answer_finite" in outputs[-1].content and "按契约原名登记" in outputs[-1].content:
                rid = re.search(r"\[run_id: (\S+)\]", outputs[-1].content)[1]
                name, args = "record_results", {"items": [{"name": "answer", "value": "6", "run_id": rid}]}
            else:
                name, args = "run_python", {"code": "answer = sum([1, 2, 3])\nprint('answer', answer)"}
            return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{len(messages)}"}])
    model = InventoryModel(loose_number_once=False)
    ctx = GraphContext.create(str(tmp_path), planner_llm=model, max_solver_steps=6, max_review_rounds=0)
    task, runs = branch(), []
    async def go():
        try:
            return await _solve_loop(ctx, task, runs, [HumanMessage(content=build_task(task, ctx))])
        finally:
            await ctx.kernels.close()
    status, error, submitted, _ = asyncio.run(go())
    assert status == "completed" and error is None and len(runs) == 1
    assert submitted["requirement_evidence"][0]["evidence_refs"] == [next(iter(ctx.registry.results))]


@pytest.mark.parametrize("valid_mixed", [False, True])
def test_mixed_operations_and_report_finish_operations_before_retrying_submission(tmp_path, valid_mixed):
    model = MixedReportModel(valid_mixed=valid_mixed, loose_number_once=False)
    ctx = GraphContext.create(str(tmp_path), planner_llm=model, max_solver_steps=12, max_review_rounds=0)
    task, runs = branch(), []
    async def go():
        try:
            return await _solve_loop(ctx, task, runs, [HumanMessage(content=build_task(task, ctx))])
        finally:
            await ctx.kernels.close()
    status, error, submitted, _ = asyncio.run(go())
    assert status == "completed" and error is None and model.deferred
    assert len(runs) == (2 if valid_mixed else 1)
    own = next(r for r in ctx.registry.results.values() if r.name == "answer")
    assert submitted["requirement_evidence"] == [{"requirement_id": "RQ1", "evidence_refs": [own.id]}]
    transcript = json.loads((tmp_path / ".system/transcripts/Q1.json").read_text())
    for message in transcript:
        calls = message["data"].get("tool_calls", [])
        assert not (len(calls) > 1 and any(c["name"] == "SolveReport" for c in calls))


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
