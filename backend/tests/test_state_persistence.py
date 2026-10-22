"""Authority, row-level persistence, migration and scoped execution handoffs."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from ai_system.research.store import ResearchStore
from ai_system.research.views import activity, activity_progress
from ai_system.graph.agent import PaperGraphAgent
from ai_system.graph.context import GraphContext
from ai_system.research.control import worker_context, refresh_registry
from ai_system.sandbox.artifacts import capture_local_outputs, inherit_execution_files
from services.file_services.workspace_fs import WorkspaceFS
from services.chat_services.chat_history_manager import ChatHistoryManager
from helpers.contracts import store_for, submit


def test_activity_progress_changes_only_its_row_and_recovers(tmp_path):
    store = ResearchStore(tmp_path, 'research')
    store.initialize('Keep the business document stable during progress')
    def durable_business():
        with store._connect() as db:
            return (db.execute('SELECT payload FROM research').fetchall()[0][0],
                    [tuple(r) for r in db.execute('SELECT * FROM journal')])
    before = durable_business()
    with activity(SimpleNamespace(research=store), 'run_python') as item:
        item['preview'] = 'one'
        activity_progress(SimpleNamespace(research=store), item)
        assert store.snapshot()['activity'][0]['preview'] == 'one'
        assert durable_business() == before
    assert durable_business() == before
    assert store.snapshot()['activity'][0]['status'] == 'completed'
    store.record_activity({'id':'unfinished', 'started_at':2, 'status':'running'})
    store.recover()
    assert next(a for a in store.snapshot()['activity'] if a['id']=='unfinished')['status']=='interrupted'
    with store._connect() as db:
        assert 'activity' not in json.loads(db.execute('SELECT payload FROM research').fetchone()[0])


def test_cutover_imports_legacy_activity_and_chat_once(tmp_path):
    root = tmp_path / 'work'; root.mkdir()
    store = ResearchStore(root, 'old'); store.initialize('Existing study')
    item = {'id':'operation', 'started_at':12, 'status':'completed'}
    legacy = {'messages':[{'id':7, 'role':'user', 'content':'original'}], 'context':{'topic':'study'}}
    raw = json.dumps(legacy)
    (root / 'chat_history.json').write_text(raw)
    with store._transaction() as db:
        state = json.loads(db.execute('SELECT payload FROM research WHERE id=?', ('old',)).fetchone()[0])
        state['activity'] = [item]
        db.execute('UPDATE research SET payload=? WHERE id=?', (json.dumps(state), 'old'))
        db.execute('DELETE FROM domain_migrations WHERE version=8')
        db.execute('DROP TABLE chat_messages'); db.execute('DROP TABLE chat_metadata')
    migrated = ResearchStore(root, 'old')
    assert migrated.snapshot()['activity'] == [item]
    manager = ChatHistoryManager(tmp_path)
    manager.save_message('work', 'user', 'new')
    assert [m['content'] for m in manager.get_messages('work')] == ['original', 'new']
    assert (root / 'chat_history.json').read_text() == raw
    # Reopening never imports the old JSON again or replaces newer messages.
    assert len(ChatHistoryManager(tmp_path).get_messages('work')) == 2
    with migrated._connect() as db:
        assert bytes(db.execute("SELECT raw FROM legacy_records WHERE source='chat_history.json'").fetchone()[0]).decode() == raw


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
    root = store.root / '.system' / 'attempts' / order.attempt_id / 'workspace'
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
