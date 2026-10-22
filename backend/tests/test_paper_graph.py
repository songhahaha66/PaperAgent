
import asyncio
import json
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph import GroundingError, PaperGraphAgent, ResultRegistry, compile_graph
from ai_system.research.store import ResearchStore
from services.file_services.workspace_fs import WorkspaceFS

from helpers.graph import TASK, _analysis, _Structured, CANDIDATE_PROMPTS, FakeModel, FixedJudge, _make_workspace, _context


# ----------------------------------------------------------------------
# 端到端（全部离线）
# ----------------------------------------------------------------------


def test_graph_end_to_end_offline(tmp_path):
    CANDIDATE_PROMPTS.clear()
    _Structured.reviewed_q1 = False
    _Structured.audits = 0
    FakeModel.text_calls = []
    ws = _make_workspace(tmp_path)
    model = FakeModel()
    # Legacy graph fixture; trusted supervised comparison has separate real-data tests.
    ctx = _context(ws, model)
    ctx.research = ResearchStore(ws, "t")

    async def go():
        graph = compile_graph(InMemorySaver())
        try:
            return await graph.ainvoke(
                {"task": TASK}, {"configurable": {"thread_id": "t"}, "recursion_limit": 100}, context=ctx
            )
        finally:
            await ctx.kernels.close()

    result = asyncio.run(go())

    # Research tools allocate real IDs; writing remains a research-level delivery.
    business=ctx.research.snapshot()
    by_title={task['subproblem']['title']:key for key,task in business['experiments'].items()}
    first=by_title['用电量预测'];second=by_title['政策评估']
    assert len(by_title)==2 and all(task['kind']=='compute' for task in business['experiments'].values())
    assert business['experiments'][second]['dependencies']==[first]
    assert result['data_profile']=='', 'Material inspection remains optional'
    design=result['designs'][first]
    assert design['controller_selected'] and not design['scores']
    assert design['candidates']==[design['chosen']] and design['chosen_index']==0
    assert 'exploration' not in design and not CANDIDATE_PROMPTS
    assert {key:report['status'] for key,report in result['solutions'].items()}=={first:'completed',second:'completed'}
    # Approved change: semantic checks are opt-in, without automatic rewrite/review chains.
    assert all(report['reviews']==[] for report in result['solutions'].values())
    assert result['consistency']==[] and _Structured.audits==0 and not _Structured.reviewed_q1
    assert result['repair_round']==0 and result['issues']==[]
    assert not any('全文结论表' in request for request in model.text_calls)
    assert len(business['executions'])==2, 'No extra experiment or recomputation for writing/review'
    manifest=json.loads((ws/'manifest.json').read_text())['runs']
    assert manifest and all(run['status']=='success' for run in manifest.values())
    from ai_system.modules.experiments.execution import solution_code_path
    for experiment_id in (first,second):
        exported=(ws/solution_code_path(experiment_id)).read_text(encoding='utf-8')
        assert 'forecast = 1234.5678' in exported and 'plt.plot' in exported

    registry=ResultRegistry.load(WorkspaceFS(str(ws)))
    assert sorted((r.subproblem,r.kind) for r in registry.results.values())==[
        (first,'figure'),(first,'number'),(second,'figure'),(second,'number')]
    assert all(r.value!='99.99' for r in registry.results.values())
    for key in (first,second):
        number=next(r for r in registry.results.values() if r.subproblem==key and r.kind=='number')
        figure=next(r for r in registry.results.values() if r.subproblem==key and r.kind=='figure')
        assert (ws/figure.path).is_file()
        own_claims=[c for c in registry.claims.values() if c.subproblem==key]
        assert len(own_claims)==1 and own_claims[0].evidence==[number.id] and own_claims[0].strength=='倾向'
        question=business['questions'][business['experiments'][key]['question_id']]
        assert question['coverage']=='answered' and number.id in question['artifact_refs'] and question['stop_reason']
    paper=(ws/'paper.md').read_text(encoding='utf-8')
    assert paper.startswith('# '+_analysis().title)
    assert '1234.57 万千瓦时' in paper and '8.5%' in paper
    assert all(r.path in paper for r in registry.results.values() if r.kind=='figure')
    assert '{{' not in paper and '12.345' in paper, 'Literal numbers do not create an automatic semantic gate'
    assert '政策评估' in paper
    assert '{{R:R1}}' in (ws/'.system/paper_raw.md').read_text(encoding='utf-8')
    assert _analysis().outline[0]['key']=='recommendations'
    from ai_system.research.control import public_snapshot
    progress=public_snapshot(business)
    assert all(t['progress']['state']=='accepted' for t in progress['experiments'])
    assert progress['delivery_stages']['render']['status']=='completed'
    assert not (ws/'plan.json').exists()


# ----------------------------------------------------------------------
# 单元
# ----------------------------------------------------------------------


def test_registry_rejects_failed_or_unknown_runs(tmp_path):
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "x 1.5")
    fs.finish_run(run_id, "error")
    registry = ResultRegistry.load(fs)
    with pytest.raises(GroundingError):
        registry.record_value("Q1", "x", "1.5", run_id)
    with pytest.raises(GroundingError):
        registry.record_value("Q1", "x", "1.5", "run_missing")


def test_registry_rejects_composite_output_lines(tmp_path):
    """整行输出登记成一条结果，正文引用时会把原始输出贴进论文"""
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "ATTR word_length coef -0.0377 p 0 valAUC 0.500\nlag7 p=0.3261 lag14 p=0.8776\n"
                               "order (0,1,1)(1,0,1)_7 range 2022-01-07..2022-10-20")
    fs.finish_run(run_id, "success")
    registry = ResultRegistry.load(fs)
    for value in ("ATTR word_length coef -0.0377 p 0 valAUC 0.500", "lag7 p=0.3261 lag14 p=0.8776"):
        with pytest.raises(GroundingError, match="单独登记"):
            registry.record_value("Q1", "x", value, run_id)
    assert registry.record_value("Q1", "p", "0.3261", run_id).kind == "number"
    assert registry.record_value("Q1", "order", "(0,1,1)(1,0,1)_7", run_id).kind == "text"
    assert registry.record_value("Q1", "range", "2022-01-07..2022-10-20", run_id).kind == "text"


def test_claims_preserve_explicit_refs_and_never_infer_identity_from_equal_numbers(tmp_path):
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "r2 0.883 coef -0.0079 p 1.04e-164 cost 3.50 dup 0.5 0.5 n 15082")
    fs.finish_run(run_id, "success")
    registry = ResultRegistry.load(fs)
    r2 = registry.record_value("Q1", "R2", "0.883", run_id)
    coef = registry.record_value("Q1", "系数", "-0.0079", run_id)
    registry.record_value("Q1", "p值", "1.04e-164", run_id)
    registry.record_value("Q1", "成本", "3.50", run_id, unit="元")
    registry.record_value("Q1", "甲", "0.5", run_id)
    registry.record_value("Q1", "乙", "0.5", run_id)
    registry.record_value("Q1", "样本量", "15082", run_id)
    other = registry.record_value("Q2", "别的", "0.883", run_id)
    literal='R²=0.883，系数 -0.0079（p=1.04e-164），成本 3.50 元，两组都是 0.5，样本 15082，$x=0.883$，0.8830'
    statement=literal+'，已有 {{R:'+other.id+'}}'
    assert registry.ground_statement('Q1',statement)==statement
    explicit='r=0.883（{{R:'+r2.id+'}}），再写 -0.0079 ({{R:'+coef.id+'}})，-0.0079'
    assert registry.ground_statement('Q1',explicit)==explicit
    [unlinked]=registry.set_claims('Q1',[{'statement':literal,'strength':'倾向'}])
    assert unlinked.statement==literal and unlinked.evidence==[]
    [linked]=registry.set_claims('Q1',[{'statement':explicit,'strength':'倾向'}])
    assert linked.statement==explicit and linked.evidence==[r2.id,coef.id]
    reloaded=ResultRegistry.load(fs)
    assert reloaded.claims[linked.id]==linked
    assert '0.883⟨'+r2.id+'⟩' in reloaded.claims_table()


def test_step_budget_nudges_near_limit_counting_since_last_human():
    from langchain.agents.middleware import ModelRequest

    from ai_system.graph.compaction import StepBudgetMiddleware

    def history(old_steps, new_steps):
        msgs = [HumanMessage(content="任务")]
        for i in range(old_steps):
            msgs += [AIMessage(content="", tool_calls=[{"name": "run_python", "args": {"code": "1"}, "id": f"a{i}"}]),
                     ToolMessage(content="ok", tool_call_id=f"a{i}")]
        msgs.append(HumanMessage(content="返工"))
        for i in range(new_steps):
            msgs += [AIMessage(content="", tool_calls=[{"name": "run_python", "args": {"code": "1"}, "id": f"b{i}"}]),
                     ToolMessage(content="ok", tool_call_id=f"b{i}")]
        return msgs

    seen = []

    async def handler(request):
        seen.append((request.messages, request.tools))
        return None

    middleware = StepBudgetMiddleware(budget=12, reserve=3, message="请交报告")
    for msgs in (history(20, 8), history(0, 9), history(0, 11)):
        asyncio.run(middleware.awrap_model_call(ModelRequest(model=None, messages=msgs, tools=["run_python"]), handler))
    # 返工前的 20 步不计入；第二次已到 9 步，追加提醒；最后一步只留报告工具
    assert seen[0][0][-1].content == "ok" and seen[0][1] == ["run_python"]
    assert "还剩约 3 步。请交报告" in seen[1][0][-1].content and seen[1][1] == ["run_python"]
    assert "步数已用完" in seen[2][0][-1].content and seen[2][1] == []
    from langgraph.errors import GraphRecursionError
    with pytest.raises(GraphRecursionError):
        asyncio.run(middleware.awrap_model_call(ModelRequest(model=None, messages=history(0, 12), tools=[]), handler))
    assert len(seen) == 3, "invalid final reports cannot keep calling the model past the step budget"


def test_record_results_requires_explicit_run_even_when_recent_output_has_the_value(tmp_path):
    from types import SimpleNamespace

    from ai_system.modules.experiments.execution import build_tools

    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "permutation p = 0.0123")
    fs.finish_run(run_id, "success")
    registry = ResultRegistry.load(fs)
    ctx = SimpleNamespace(registry=registry, kernels=SimpleNamespace(get=lambda key: None), comparison_proof=None)
    run_log = [{"run_id": run_id, "code": "", "ok": True}]
    record = {t.name: t for t in build_tools(ctx, "Q2", run_log)}["record_results"]
    reply = asyncio.run(record.coroutine([{"name": "p值", "value": "0.0123", "run_id": run_id[:-3] + "zzz"}]))
    assert "登记失败" in reply and not registry.results
    reply = asyncio.run(record.coroutine([{"name": "p值", "value": "0.0123", "run_id": run_id}]))
    assert "{{R:R1}}" in reply and registry.get("R1").run_id == run_id
    # 最近的执行里也没有这个值：照样拒绝
    reply = asyncio.run(record.coroutine([{"name": "编造", "value": "0.9876", "run_id": "run_x"}]))
    assert "登记失败" in reply and len(registry.results) == 1


def test_malformed_tool_call_is_retried_and_never_left_in_history():
    from ai_system.graph.compaction import MALFORMED_NOTE, TruncationGuardMiddleware, drop_invalid_calls

    class Request:
        def __init__(self, messages):
            self.messages = messages

        def override(self, messages):
            return Request(messages)

    bad = {"name": "run_python", "args": '{"code": "print(1', "id": "c1", "error": "bad json", "type": "invalid_tool_call"}
    seen = []

    async def handler(request):
        seen.append(request.messages)
        return AIMessage(content="", invalid_tool_calls=[bad], response_metadata={"finish_reason": "tool_calls"})

    result = asyncio.run(TruncationGuardMiddleware().awrap_model_call(Request([HumanMessage(content="go")]), handler))
    assert len(seen) == 2 and seen[1][-1].content == MALFORMED_NOTE
    # 重试仍然坏掉时，坏调用不留在历史里（否则下一次请求被接口以"工具调用没有结果"拒绝）
    assert result.invalid_tool_calls == []
    assert drop_invalid_calls(AIMessage(content="x")).content == "x"


def test_explicit_new_run_does_not_resume_an_interrupted_plan(tmp_path):
    ws = _make_workspace(tmp_path)
    agent = PaperGraphAgent(FakeModel(), workspace_dir=str(ws), judge=FixedJudge(), confirm_models=True)
    asyncio.run(agent.run(TASK))
    first_thread = agent._load_thread()
    assert not agent.waiting_confirm
    asyncio.run(agent.run("另一项研究", new_run=True))
    assert not agent.waiting_confirm
    assert agent._load_thread() != first_thread
