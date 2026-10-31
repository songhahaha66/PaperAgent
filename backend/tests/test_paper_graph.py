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

    business=ctx.research.snapshot()
    assert len(business['questions'])==2 and not business['experiments'] and not business['executions']
    assert result['data_profile']=='' and result['designs']=={} and not CANDIDATE_PROMPTS
    assert result['solutions']=={} and not business['artifacts']
    assert result['consistency']==[] and _Structured.audits==0 and not _Structured.reviewed_q1
    assert result['repair_round']==0 and result['issues']==[]
    assert not any('全文结论表' in request for request in model.text_calls)
    manifest=json.loads((workspace_path(ws, 'manifest.json')).read_text())['runs']
    assert len(manifest)==4 and all(run['status']=='success' for run in manifest.values())
    assert all(sum(r['question_id']==qid for r in manifest.values())==2 for qid in business['questions'])
    exported=(ws/'code/research_solution.py').read_text(encoding='utf-8')
    assert 'forecast = 1234.5678' in exported and 'plt.plot' in exported
    assert not any(a['capability']=='experiments.execute' for a in business['activity'])
    registry=ResultRegistry.load(WorkspaceFS(str(ws)))
    assert registry.results=={} and registry.claims=={}
    for qid,q in business['questions'].items():
        runs=[r for r in result['runs'] if r['question_id']==qid]
        files=[p for r in runs for p in r['files']]
        assert len(runs)==2 and any(p.endswith('.png') for p in files)
        assert all((ws/p).is_file() for p in files)
        assert q['coverage']=='answered' and q['stop_reason']
    paper=(ws/'paper.md').read_text(encoding='utf-8')
    assert paper.startswith('# '+_analysis().title) and '政策评估' in paper
    assert '{{' not in paper and '12.345' not in paper
    assert not ctx.planner_llm.text_calls
    assert not workspace_path(ws,'.system/paper_raw.md').exists(), 'current source is delivered without a second raw manuscript copy'
    from ai_system.research.control import public_snapshot
    progress=public_snapshot(business)
    assert not progress['experiments'] and all(q['coverage']=='answered' for q in progress['questions'])
    assert progress['delivery_stages']['export']['status']=='completed'
    assert not (ws/'plan.json').exists()


# ----------------------------------------------------------------------
# 单元
# ----------------------------------------------------------------------


def test_malformed_tool_call_is_retried_and_never_left_in_history():
    from ai_system.graph.compaction import MALFORMED_NOTE, MalformedToolMiddleware, drop_invalid_calls

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

    result = asyncio.run(MalformedToolMiddleware().awrap_model_call(Request([HumanMessage(content="go")]), handler))
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
