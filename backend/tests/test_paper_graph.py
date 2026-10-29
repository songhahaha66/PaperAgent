from ai_system.sandbox.evidence import seal_run
from services.file_services.workspace_layout import workspace_path

import asyncio
import json
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph import PaperGraphAgent, ResultRegistry, compile_graph
from ai_system.research.store import ResearchStore
from services.file_services.workspace_fs import WorkspaceFS

from helpers.graph import TASK, _analysis, _Structured, CANDIDATE_PROMPTS, FakeModel, _make_workspace, _context


# ----------------------------------------------------------------------
# 端到端（全部离线）
# ----------------------------------------------------------------------


pytestmark = pytest.mark.usefixtures("offline_kernel")

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
    ctx.research.initialize(TASK)

    async def go():
        graph = compile_graph(InMemorySaver())
        try:
            return await graph.ainvoke(
                {"research_blocked": False}, {"configurable": {"thread_id": "t"}, "recursion_limit": 100}, context=ctx
            )
        finally:
            await ctx.kernels.close()

    checkpoint = asyncio.run(go())
    assert set(checkpoint) == {"research_blocked"}
    result = PaperGraphAgent._result(ctx)

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
    manifest=json.loads((workspace_path(ws, 'manifest.json')).read_text())['runs']
    assert manifest and all(run['status']=='success' for run in manifest.values())
    from ai_system.modules.experiments.execution import solution_code_path
    for experiment_id in (first,second):
        exported=(ws/solution_code_path(experiment_id)).read_text(encoding='utf-8')
        assert 'forecast = 1234.5678' in exported and 'plt.plot' in exported

    registry=ResultRegistry.load(WorkspaceFS(str(ws)))
    assert registry.results=={} and registry.claims=={}
    for key in (first,second):
        report=result['solutions'][key]
        assert report['runs'] and any(p.endswith('.png') for p in report['files'])
        assert all((ws/p).is_file() for p in report['files'])
        question=business['questions'][business['experiments'][key]['question_id']]
        assert question['coverage']=='answered' and question['stop_reason']
    paper=(ws/'paper.md').read_text(encoding='utf-8')
    assert paper.startswith('# '+_analysis().title) and '政策评估' in paper
    assert '{{' not in paper and '12.345' in paper
    assert '{{R:' not in workspace_path(ws,'.system/paper_raw.md').read_text()
    assert _analysis().outline[0]['key']=='recommendations'
    from ai_system.research.control import public_snapshot
    progress=public_snapshot(business)
    assert all(t['progress']['state']=='accepted' for t in progress['experiments'])
    assert progress['delivery_stages']['render']['status']=='completed'
    assert not (ws/'plan.json').exists()


# ----------------------------------------------------------------------
# 单元
# ----------------------------------------------------------------------


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
    agent = PaperGraphAgent(FakeModel(), workspace_dir=str(ws))
    asyncio.run(agent.run(TASK))
    first_thread = agent._load_thread()
    assert not agent.waiting_confirm
    asyncio.run(agent.run("另一项研究", new_run=True))
    assert not agent.waiting_confirm
    assert agent._load_thread() != first_thread
