"""Exercise native LangGraph/LangChain tracing without making network requests."""
import asyncio
import json
from unittest.mock import Mock

import pytest
from langsmith import Client, get_tracing_context
from langsmith import run_trees
from langsmith.run_helpers import get_current_run_tree

from ai_system.config.observability import research_tracing, tracing_status
from ai_system.modules.registry import CapabilityProduct, CapabilityRequest, ModuleRegistry
from evals.langsmith_smoke import run_smoke


@pytest.fixture
def recorder(monkeypatch):
    client = Mock(spec=Client)
    monkeypatch.setattr(run_trees, 'get_cached_client', lambda **kwargs: client)
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    monkeypatch.setenv('LANGSMITH_API_KEY', 'test-secret-never-traced')
    monkeypatch.setenv('LANGSMITH_PROJECT', 'PaperAgent-test')
    return client


def test_native_graph_model_and_capability_share_one_trace(recorder):
    run_id = asyncio.run(run_smoke())
    runs = [c.kwargs for c in recorder.create_run.call_args_list]
    roots = [r for r in runs if not r.get('parent_run_id')]
    assert len(roots) == 1
    assert roots[0]['id'] == run_id
    assert roots[0]['name'] == 'PaperAgent.smoke'
    assert {'chain', 'llm', 'tool'} <= {r['run_type'] for r in runs}
    assert any(r['name'] == 'smoke.check' for r in runs)
    assert all(r['trace_id'] == run_id for r in runs)
    for run in runs:
        metadata = run['extra']['metadata']
        assert metadata['work_id'] == 'langsmith-smoke'
        assert metadata['thread_id'] == str(run_id)
        assert metadata['research_id'] == str(run_id)
        assert run['session_name'] == 'PaperAgent-test'
    assert 'test-secret-never-traced' not in str(runs)
    assert any(c.kwargs.get('outputs') for c in recorder.update_run.call_args_list)


def test_disabled_or_missing_key_produces_no_remote_calls(recorder, monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    assert tracing_status() == 'disabled'
    asyncio.run(run_smoke())
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    monkeypatch.delenv('LANGSMITH_API_KEY')
    assert tracing_status() == 'missing_key'
    asyncio.run(run_smoke())
    registry = ModuleRegistry()
    registry.register('test', 'test.ok', 'experiment', lambda ctx, inputs: CapabilityProduct('experiment', 7))
    assert registry.invoke_sync(CapabilityRequest(name='test.ok'), None).value == 7
    recorder.create_run.assert_not_called()


def test_concurrent_contexts_and_errors_keep_identity_and_restore_context(recorder):
    registry = ModuleRegistry()

    async def fail(ctx, inputs):
        await asyncio.sleep(0)
        assert get_current_run_tree().metadata['work_id'] == ctx
        raise ValueError('capability failed')

    registry.register('test', 'test.fail', 'experiment', fail)

    async def worker(work):
        with research_tracing(work, 'research-' + work, 'research'):
            with pytest.raises(ValueError, match='capability failed'):
                await registry.invoke(CapabilityRequest(name='test.fail'), work)

    async def both():
        original = get_tracing_context()
        await asyncio.gather(worker('A'), worker('B'))
        assert get_tracing_context() == original

    asyncio.run(both())
    runs = [c.kwargs for c in recorder.create_run.call_args_list]
    assert {r['extra']['metadata']['work_id'] for r in runs} == {'A', 'B'}
    assert all('capability failed' in c.kwargs['error'] for c in recorder.update_run.call_args_list)


def test_sync_capability_records_output_and_type_errors(recorder):
    registry = ModuleRegistry()
    registry.register('test', 'test.ok', 'experiment', lambda ctx, inputs: CapabilityProduct('experiment', 7))
    registry.register('test', 'test.wrong', 'experiment', lambda ctx, inputs: CapabilityProduct('paper', {}))
    with research_tracing('A', 'research-A', 'research'):
        assert registry.invoke_sync(CapabilityRequest(name='test.ok'), object()).value == 7
        with pytest.raises(TypeError, match='产物'):
            registry.invoke_sync(CapabilityRequest(name='test.wrong'), object())
    updates = [c.kwargs for c in recorder.update_run.call_args_list]
    assert updates[0]['outputs'] == {'kind': 'experiment', 'value': 7}
    assert 'TypeError' in updates[1]['error']


def test_paper_agent_keeps_local_callback_and_native_trace(recorder, tmp_path):
    from types import SimpleNamespace
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langgraph.graph import END, START, StateGraph
    from ai_system.graph.agent import PaperGraphAgent

    model = FakeListChatModel(responses=['ok'])

    async def observe(state):
        return {'answer': (await model.ainvoke(state['question'])).content}

    from evals.langsmith_smoke import SmokeState
    builder = StateGraph(SmokeState)
    builder.add_node('observe', observe)
    builder.add_edge(START, 'observe')
    builder.add_edge('observe', END)
    compiled = builder.compile()

    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values={})

        async def ainvoke(self, state, config, context):
            return await compiled.ainvoke(state, config)

    agent = PaperGraphAgent(model, workspace_dir=str(tmp_path), work_id='project-A', judge=None)
    ctx = agent._context()
    # This small graph only tests the dispatch/tracing boundary, not delivery.
    agent._final_message = lambda result, ctx: 'done'
    result = asyncio.run(agent._invoke(Graph(), {'question': 'hi'}, {}, 'research-A', ctx))
    assert result == 'done'
    calls = json.loads((tmp_path / '.system/llm_calls.json').read_text())
    assert calls
    roots = [c.kwargs for c in recorder.create_run.call_args_list if not c.kwargs.get('parent_run_id')]
    assert len(roots) == 1 and roots[0]['name'] == 'PaperAgent.research'
    assert roots[0]['extra']['metadata']['work_id'] == 'project-A'


def test_comment_trace_omits_store_and_model_objects(recorder, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from services.chat_services import paper_comments
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    store = SimpleNamespace(root=tmp_path / 'project-A', research_id='research-A',
                            credential='store-secret-never-traced')
    model = FakeListChatModel(responses=['explanation'])

    async def execute(store, execution_id, llm, *, resume):
        reply = await llm.ainvoke('Explain this passage')
        return {'id': execution_id, 'state': 'completed', 'answer': reply.content}

    monkeypatch.setattr(paper_comments, '_execute', execute)
    assert asyncio.run(paper_comments.execute(store, 'execution-A', model))['answer'] == 'explanation'
    runs = [c.kwargs for c in recorder.create_run.call_args_list]
    root = next(r for r in runs if not r.get('parent_run_id'))
    assert root['name'] == 'PaperAgent.comment'
    assert root['inputs'] == {'execution_id': 'execution-A', 'resume': False}
    assert all(r['extra']['metadata']['research_id'] == 'research-A' for r in runs)
    assert any(r['run_type'] == 'llm' and r['parent_run_id'] == root['id'] for r in runs)
    assert 'store-secret-never-traced' not in str(runs)


def test_controller_model_failure_is_a_failed_native_tool_agent_trace(recorder,tmp_path):
    from ai_system.research.planner import Planner,decision_context
    from ai_system.graph.context import GraphContext
    from ai_system.research.store import ResearchStore
    from research_tools_helpers import ResearchToolsModel
    def fail(messages):raise RuntimeError('controlled research model failure')
    model=ResearchToolsModel(respond=fail)
    ctx=GraphContext.create(str(tmp_path),planner_llm=model);ctx.research=ResearchStore(tmp_path,'research-A')
    ctx.research.initialize('Observe supplied material')
    _,context=decision_context(ctx.research,ctx.research.snapshot())
    with research_tracing('project-A','research-A','research'):
        with pytest.raises(RuntimeError,match='controlled research model failure'):
            asyncio.run(Planner(model).run(ctx,context,'observation:trace'))
    runs=[call.kwargs for call in recorder.create_run.call_args_list]
    assert any(run['run_type']=='llm' for run in runs)
    assert all(run['extra']['metadata']['work_id']=='project-A' for run in runs)
    assert any('controlled research model failure' in str(call.kwargs.get('error','')) for call in recorder.update_run.call_args_list)
    assert 'ResearchStore(' not in str(runs) and 'test-secret-never-traced' not in str(runs)
