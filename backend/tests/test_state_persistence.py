"""Authority, row-level persistence, migration and scoped execution handoffs."""
from services.file_services.workspace_layout import workspace_path
import asyncio
import json
from types import SimpleNamespace

import pytest
from ai_system.research.store import ResearchStore
from ai_system.agent.context import new_turn
from ai_system.agent.protocol import ToolItem
from ai_system.agent.agent import PaperAgent
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
    from ai_system.agent.agent import PaperAgent
    import pytest
    agent=PaperAgent(None,workspace_dir=str(tmp_path))
    agent._save_thread('missing','failed')
    with pytest.raises(ValueError,match='研究状态不存在'):
        asyncio.run(agent.resume_failed())


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


def test_loop_recovers_tool_receipt_after_crash_before_message_commit(tmp_path,monkeypatch):
    from langchain_core.messages import AIMessage,HumanMessage,ToolMessage
    from langchain_core.tools import StructuredTool
    from ai_system.agent.loop import AgentLoop,run_session
    from ai_system.agent.store import AgentStore
    from research_tools_helpers import ResearchToolsModel,tool
    from helpers.context import session_context
    import pytest
    calls=[]
    async def compute():
        calls.append('submitted');return 'verified result'
    def respond(messages):
        if any(isinstance(m,ToolMessage) for m in messages):
            assert any(isinstance(m,AIMessage) and m.additional_kwargs.get('reasoning_content')=='provider continuation' for m in messages)
            return AIMessage(content='done')
        value=tool('compute',{},'one-call');value.additional_kwargs['reasoning_content']='provider continuation';return value
    model=ResearchToolsModel(respond=respond);ctx=session_context(tmp_path,model)
    agent=AgentLoop(model,[StructuredTool.from_function(coroutine=compute,description='Compute once')],'Compute.',ctx=ctx)
    append=AgentStore.append_message
    def interrupt(self,session,message):
        if isinstance(message,ToolMessage):raise RuntimeError('crash after tool receipt')
        return append(self,session,message)
    monkeypatch.setattr(AgentStore,'append_message',interrupt)
    with pytest.raises(RuntimeError,match='crash after tool receipt'):
        asyncio.run(run_session(agent,'lead',[HumanMessage(content='go',id='request')],ctx=ctx))
    monkeypatch.setattr(AgentStore,'append_message',append)
    result=asyncio.run(run_session(agent,'lead',[HumanMessage(content='go',id='request')],ctx=ctx))
    assert result['status']=='completed' and result['messages'][-1].content=='done'
    assert calls==['submitted'] and len([m for m in result['messages'] if isinstance(m,ToolMessage)])==1
    assert AgentStore(tmp_path).session('lead')['status']=='completed'
