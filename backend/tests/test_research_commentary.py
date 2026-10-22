"""Public progress stays ordered with real tools and never republishes reasoning."""
import asyncio
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from langchain_core.messages import AIMessage
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.graph.commentary import ProgressMiddleware, public_progress
from test_conversation_timeline import writer, call


def test_only_explicit_public_blocks_are_projected():
    assert public_progress('ordinary internal prose') == []
    assert public_progress('<think><progress>hidden</progress></think><progress>Ready.</progress>') == ['Ready.']
    assert public_progress('<think><progress>unterminated reasoning</progress>') == []
    assert public_progress([{'type':'reasoning','text':'<progress>hidden</progress>'},
                            {'type':'text','text':'<progress>Reading the supplied file.</progress>'}]) == ['Reading the supplied file.']
    assert public_progress('<progress>```python\nprint(1)```</progress>') == []
    assert public_progress('<progress>'+'x'*801+'</progress>') == []


def test_model_progress_commits_before_tool_and_survives_refresh(tmp_path):
    manager, root, reply, updates = writer(tmp_path)
    class Events:
        async def emit(self, kind, content): reply.block({'type':kind,'content':content})
    middleware = ProgressMiddleware(Events(), scope='experiment:E1')
    response = SimpleNamespace(result=[AIMessage(id='msg-1', content='<progress>I will inspect the data.</progress>',
        tool_calls=[{'name':'read_file','args':{},'id':'tool-1','type':'tool_call'}])])
    async def handler(_): return response
    async def scenario():
        assert await middleware.awrap_model_call(None, handler) is response
        # The model wrapper completes before the agent dispatches its tool.
        assert manager.get_messages('work')[0]['segments'][0]['text'] == 'I will inspect the data.'
        reply.execution(root,'r','activity',call())
        await middleware.awrap_model_call(None, handler)
        response.result = [AIMessage(id='msg-2',content='<progress>The file contains 20 rows.</progress>')]
        await middleware.awrap_model_call(None,handler)
        reply.execution(root,'r','activity',call(status='completed',finished_at=2))
        reply.finish('completed')
    asyncio.run(scenario())
    stored = manager.get_messages('work')[0]
    assert [s['type'] for s in stored['segments']] == ['text','execution','text']
    assert stored['segments'][0]['phase'] == 'commentary'
    assert stored['segments'][0]['scope'] == 'experiment:E1'
    assert stored['segments'][1]['execution']['status'] == 'completed'
    assert len(stored['segments']) == 3
    assert all('<progress>' not in str(u) for u in updates)


def test_distinct_parallel_messages_and_final_text_do_not_coalesce(tmp_path):
    manager, root, reply, _ = writer(tmp_path)
    for scope in ['E1','E2']:
        event={'type':'research_commentary','content':{'id':hashlib.sha256(scope.encode()).hexdigest(),
            'text':f'Working on {scope}', 'scope':scope}}
        reply.block(event); reply.block(event)
    reply.text('Final answer.')
    reply.finish('completed')
    reply.block({'type':'research_commentary','content':{'id':'f'*64,'text':'Late'}})
    stored=manager.get_messages('work')[0]
    assert [s['text'] for s in stored['segments']] == ['Working on E1','Working on E2','Final answer.']


def test_failed_worker_report_is_failed_activity_even_when_call_returns(tmp_path, monkeypatch):
    from ai_system.graph.context import GraphContext
    from ai_system.research.store import ResearchStore
    from ai_system.research.control import experiment_dispatch
    from ai_system.modules.experiments.planning import experiment_design
    from ai_system.modules.registry import invoke
    from research_tools_helpers import add_experiment
    from services.chat_services.conversation_timeline import TIMELINE
    manager, root, reply, _ = writer(tmp_path)
    ctx = GraphContext.create(str(root), planner_llm=None)
    ctx.research = ResearchStore(root, 'failed-worker')
    ctx.research.initialize('Compute a small sample mean.')
    eid = add_experiment(ctx.research, 'What is the mean?')
    async def solve(branch, runtime):
        return {'report': {'status': 'failed', 'error': 'Model service unavailable', 'summary': 'Saved calculations'},
                'design': experiment_design(branch['experiment'])}
    monkeypatch.setattr('ai_system.modules.experiments.execution.solve', solve)
    async def run():
        token = TIMELINE.set(reply)
        try:
            result = await invoke(ctx, 'experiments.execute', branch=experiment_dispatch(ctx, eid))
            assert '_execution_outcome' not in result
        finally:
            TIMELINE.reset(token)
            await ctx.kernels.close()
    asyncio.run(run())
    assert ctx.research.snapshot()['experiments'][eid]['execution_status'] == 'failed'
    row = next(s['execution'] for s in manager.get_messages('work')[0]['segments']
               if s.get('execution', {}).get('capability') == 'experiments.execute')
    assert row['status'] == 'failed' and row['finished_at']
    assert row['message'] == 'Model service unavailable'
