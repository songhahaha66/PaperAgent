"""Authority, row-level persistence, migration and scoped execution handoffs."""
from services.file_services.workspace_layout import workspace_path
import asyncio
import json
from types import SimpleNamespace

import pytest
from ai_system.research.store import ResearchStore
from ai_system.agent.context import new_turn
from ai_system.agent.protocol import ToolItem
from ai_system.graph.agent import PaperGraphAgent
from ai_system.graph.context import GraphContext
from helpers.legacy_research import worker_context
from ai_system.research.control import refresh_registry
from ai_system.sandbox.artifacts import capture_local_outputs, inherit_execution_files
from services.file_services.workspace_fs import WorkspaceFS
from services.chat_services.chat_history_manager import ChatHistoryManager
from helpers.contracts import store_for, submit


def test_item_progress_is_independent_of_research_state_and_recovers(tmp_path):
    store = ResearchStore(tmp_path, 'research')
    store.initialize('Keep the business document stable during progress')
    def durable_business():
        with store._connect() as db:
            return (db.execute('SELECT payload FROM research').fetchall()[0][0],
                    [tuple(r) for r in db.execute('SELECT * FROM journal')])
    before = durable_business()
    turn = new_turn(tmp_path,'lead',research_id='research')
    item = ToolItem(id='unfinished',thread_id='lead',turn_id=turn.turn_id,type='commandExecution',
                    tool_name='run_python',call_id='call',label='计算',started_at=2)
    turn.put(item.model_dump(),'item/started')
    item.output_tail='one'
    turn.put(item.model_dump(),'item/updated')
    assert store.snapshot()['items'][0]['output_tail']=='one'
    assert durable_business() == before
    store.recover()
    assert store.snapshot()['items'][0]['status']=='interrupted'
    with store._connect() as db:
        assert 'items' not in json.loads(db.execute('SELECT payload FROM research').fetchone()[0])
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='research_activity'").fetchone()


def test_agent_store_does_not_read_old_activity_or_message_segments(tmp_path):
    from ai_system.agent.store import AgentStore
    from services.chat_services.conversation_timeline import public_reply
    store=ResearchStore(tmp_path,'study');store.initialize('Existing study')
    with store._transaction() as db:
        db.execute('CREATE TABLE research_activity(payload TEXT)')
        db.execute('INSERT INTO research_activity VALUES (?)',(json.dumps({'id':'old','status':'completed'}),))
    assert AgentStore(tmp_path,read_only=True).items()==[]
    assert 'segments' not in public_reply({'id':'old','segments':[{'type':'execution'}]})


def test_research_recovery_closes_unfinished_chat_text_in_its_turn(tmp_path):
    from ai_system.agent.protocol import MessageItem
    store = ResearchStore(tmp_path, 'research'); store.initialize('Resume after a crash')
    # Browser Threads are workspace-scoped; only their tool Items carry research_id.
    old = new_turn(tmp_path, 'work')
    old.put(MessageItem(id='text', thread_id='work', turn_id=old.turn_id,
                        type='agentMessage', text='Partial answer', started_at=1).model_dump(), 'item/started')
    old.put(ToolItem(id='tool', thread_id='work', turn_id=old.turn_id, research_id='research',
                     type='toolCall', tool_name='read_file', call_id='read', label='Read',
                     started_at=2).model_dump(), 'item/started')
    current = new_turn(tmp_path, 'work')
    current.put(MessageItem(id='new-text', thread_id='work', turn_id=current.turn_id,
                            type='agentMessage', text='New request', started_at=3).model_dump(), 'item/started')
    store.recover()
    recovered = old.store.turn_snapshot(old.turn_id)
    assert recovered['turn']['status'] == 'interrupted'
    assert all(item['status'] == 'interrupted' for item in recovered['items'])
    assert current.store.turn_snapshot(current.turn_id)['turn']['status'] == 'running'
    assert current.store.get_item('new-text')['status'] == 'running'


def test_resume_never_rebuilds_business_from_checkpoint(tmp_path):
    agent = PaperGraphAgent(None, workspace_dir=str(tmp_path))
    ctx = agent._context(); ctx.research = ResearchStore(tmp_path, 'missing')
    class OldGraph:
        async def aget_state(self, *_):
            return SimpleNamespace(values={'task':'stale task', 'analysis':{'title':'stale'}})
        async def ainvoke(self, *_args, **_kwargs):
            pytest.fail('must not run without business state')
    with pytest.raises(ValueError, match='研究状态不存在'):
        asyncio.run(agent._resume_graph(OldGraph(), {}, 'missing', ctx))
    assert ctx.research.snapshot() is None


def execution_files(store, key):
    order = store.claim(key)
    root = store.root / 'experiments' / order.attempt_id / 'workspace'
    fs = WorkspaceFS(str(root)); fs.init_structure()
    run = fs.create_run()
    fs.write_text(f'outputs/intermediate/{key}.csv', key)
    capture_local_outputs(fs, run, {})
    fs.finish_run(run)
    submit(store, key, {'count':1}, order=order)
    return fs, run


def test_worker_copies_dependencies_and_not_independent_experiments(tmp_path):
    store = store_for(tmp_path)
    first, first_run = execution_files(store, 'Q1')
    other, other_run = execution_files(store, 'Q3')
    # Unrelated debris is neither a run output nor an explicit handoff.
    first.write_text('outputs/unrelated-old.txt', 'old')
    first.write_text('code/Q3_solution.py', 'unrelated')
    ctx = GraphContext.create(str(tmp_path), planner_llm=None); ctx.research = store
    refresh_registry(ctx)
    local = worker_context(ctx, store.claim('Q2'))
    assert local.fs.read_text('outputs/intermediate/Q1.csv') == 'Q1'
    assert not local.fs.exists('outputs/intermediate/Q3.csv')
    assert not local.fs.exists('outputs/unrelated-old.txt')
    assert not local.fs.exists('code/Q3_solution.py')
    assert set(local.fs._manifest['runs']) == {first_run}
    assert other_run not in local.fs._manifest['runs']
    assert {r.subproblem for r in local.registry.results.values()} == {'Q1'}


def test_inheritance_uses_immutable_runs_and_skips_previously_inherited_runs(tmp_path):
    source = WorkspaceFS(str(tmp_path/'source')); source.init_structure()
    first = source.create_run()
    source.write_text('outputs/base.csv', 'old')
    capture_local_outputs(source, first, {}); source.finish_run(first)
    second = source.create_run()
    source.write_text('outputs/base.csv', 'new')
    capture_local_outputs(source, second, {}); source.finish_run(second)
    source._manifest['inherited_runs'] = [first]; source._save_manifest()
    # Restore even when the mutable file has been removed.
    source.abs('outputs/base.csv').unlink()
    target = WorkspaceFS(str(tmp_path/'target')); target.init_structure()
    assert inherit_execution_files(source.root, target, 'Q1') == {second}
    assert set(target._manifest['runs']) == {second}
    assert target.read_text('outputs/base.csv') == 'new'


def test_legacy_checkpoint_cleanup_preserves_history_and_resumes_from_store(tmp_path):
    from langgraph.graph import START, END, StateGraph
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from ai_system.graph.state import PaperState
    from ai_system.graph.build import compile_graph
    from ai_system.graph.checkpoint import discard_business_mirror
    async def run():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'graph.sqlite')) as saver:
            config = {'configurable': {'thread_id':'legacy'}}
            old = StateGraph(PaperState)
            old.add_node('solve_join', lambda state: {})
            old.add_edge(START, 'solve_join'); old.add_edge('solve_join', END)
            old = old.compile(checkpointer=saver)
            await old.aupdate_state(config, {'task':'stale task','analysis':{'title':'stale title'},
                                           'sections':{'old':'stale draft'}}, as_node='solve_join')
            before = await saver.aget_tuple(config)
            ctx = GraphContext.create(str(tmp_path), planner_llm=None)
            ctx.research = ResearchStore(tmp_path, 'legacy')
            ctx.research.initialize('authoritative task'); ctx.research.set_outcome('completed')
            graph = compile_graph(saver)
            await discard_business_mirror(graph, config)
            await graph.aupdate_state(config, {'research_blocked':False}, as_node='solve_join')
            result = await graph.ainvoke(None, config, context=ctx)
            latest = (await saver.aget_tuple(config)).checkpoint['channel_values']
            assert set(result) == {'research_blocked'}
            assert not {'task','analysis','sections'} & set(latest)
            assert ctx.research.snapshot()['spec']['requirement'] == 'authoritative task'
            assert (await saver.aget_tuple(before.config)).checkpoint == before.checkpoint
            await ctx.kernels.close()
    asyncio.run(run())
