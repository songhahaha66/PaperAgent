"""Submit, receive concrete gaps, fill them using tools, and submit again."""

from helpers.submissions import branch, seed, report
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
from ai_system.modules.experiments.execution import _solve_loop, build_task, submission_schema
from helpers.graph import FakeModel


@pytest.mark.parametrize("failure", ["missing_metric", "unknown_reference", "foreign_reference", "changed_output", "failed_run", "other_attempt"])
def test_submission_rejects_missing_metrics_out_of_scope_refs_and_invalid_execution_sources(tmp_path, failure):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    runs = []
    own = seed(ctx, runs, name="wrong_name" if failure == "missing_metric" else "answer")
    payload = report(own.id,requirement=None)
    bad_ref=None
    if failure == "unknown_reference":
        bad_ref='R999'
    elif failure == "foreign_reference":
        bad_ref=seed(ctx,[],sid='Q9').id
    if bad_ref:
        payload['key_findings'][0]['statement']='额外结果 {{R:'+bad_ref+'}}'
    if failure == "changed_output":
        ctx.fs.save_run_stdout(own.run_id, "no result")
    if failure == "failed_run":
        ctx.fs.finish_run(own.run_id, "error")
    if failure == "other_attempt":
        runs.clear()
    with pytest.raises(ValidationError) as error:
        submission_schema(ctx, branch(), runs).model_validate(payload)
    assert {"missing_metric": "answer_finite", "unknown_reference": "R999", "foreign_reference": bad_ref,
            "changed_output": "来源无效", "failed_run": "执行失败", "other_attempt": "本次任务"}[failure] in str(error.value)


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
        registrations=[m for m in tools if m.name=='record_results']
        errors=[m for m in tools if m.name=='SolveReport' and '报告未提交' in str(m.content)]
        if not tools:
            name,args='run_python',{'code':"answer = sum([1, 2, 3])\nprint('answer', answer)\nprint('unrelated', 7)"}
        else:
            run_id=re.search(r'\[run_id: (\S+)\]',tools[0].content)[1]
            # Missing inventory is already returned by computation/registration
            # tools, before SolveReport is offered. Reuse that actual run.
            if self.mode!='stuck' and (not registrations or
                    self.mode=='missing_metric' and len(registrations)==1):
                self.feedback.append(str(tools[-1].content))
                assert 'answer_finite' in str(tools[-1].content)
                name,args='record_results',{'items':[{'name':'wrong_name' if self.mode=='missing_metric' and not registrations else 'answer',
                                                       'value':'6','run_id':run_id}]}
            else:
                refs=[ref for m in registrations for ref in re.findall(r'\{\{R:(R\d+)\}\}',str(m.content))]
                self.submissions+=1
                name,args='SolveReport',report(refs[-1] if refs else 'R999',requirement=None)
                if self.mode=='unknown_reference' and not errors:
                    args['summary']='未获授权的结果 {{R:R999}}'
                if errors:
                    self.feedback.append(str(errors[-1].content))
        return AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':f'call_{len(messages)}'}])


@pytest.mark.parametrize("mode,expected_reports", [("missing_registration",1), ("missing_metric",1), ("unknown_reference",2)])
def test_real_tool_feedback_fills_registration_or_reference_gaps_without_rerunning_code(tmp_path, mode, expected_reports):
    model = FillGapsModel(mode=mode, loose_number_once=False)
    ctx = GraphContext.create(str(tmp_path), planner_llm=model, max_solver_steps=12)
    task = branch()
    runs = []

    async def go():
        try:
            return await _solve_loop(ctx, task, runs, [HumanMessage(content=build_task(task, ctx))])
        finally:
            await ctx.kernels.close()

    status, error, submitted, reviews = asyncio.run(go())
    assert status == "completed" and error is None and not reviews
    assert model.feedback and model.submissions == expected_reports
    assert len(runs) == 1, "the same live kernel/output is reused to fill missing registration"
    own = next(r for r in ctx.registry.results.values() if r.name == "answer")
    assert submitted["requirement_evidence"]==[]
    assert '{{R:'+own.id+'}}' in submitted['summary'] and '{{R:'+own.id+'}}' in submitted['key_findings'][0]['statement']
    assert own.run_id==runs[0]['run_id'] and runs[0]['ok']
    ctx.registry.verify(own.value,own.run_id)
    assert len([r for r in ctx.registry.results.values() if r.name=='answer'])==1
    transcript = json.loads((tmp_path / ".system/transcripts/Q1.json").read_text())
    observations=[m['data'] for m in transcript if m['type']=='tool']
    assert any('answer_finite' in str(m.get('content')) for m in observations)
    if mode=='unknown_reference':
        assert any(m.get('name')=='SolveReport' and 'R999' in str(m.get('content')) and '报告未提交' in str(m.get('content')) for m in observations)
    else:
        assert any(m.get('name')=='record_results' and '必需登记指标已齐' in str(m.get('content')) for m in observations)


def test_repeated_invalid_submissions_exhaust_budget_without_a_fake_final_report(tmp_path):
    model = FillGapsModel(mode="stuck", loose_number_once=False)
    ctx = GraphContext.create(str(tmp_path), planner_llm=model, max_solver_steps=5)
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
