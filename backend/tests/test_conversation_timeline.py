"""Offline acceptance cases for ordered durable conversation replies.

Authored for parent review; no model, server or browser is required.
"""
import json
import multiprocessing
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.chat_services.chat_history_manager import ChatHistoryManager
from services.chat_services.conversation_timeline import ReplyWriter, resolve_executions, safe_file


def writer(tmp_path, publish=None):
    manager = ChatHistoryManager(str(tmp_path))
    root = tmp_path / 'work'
    updates = []
    reply = ReplyWriter(manager, 'work', 'chat-task', root, publish or updates.append)
    return manager, root, reply, updates


def call(identity='call-1', **values):
    return {'id': identity, 'capability': 'read_file', 'started_at': 1, 'status': 'running', **values}


def test_interleaving_checkpoint_before_publish_and_same_row_update(tmp_path):
    manager, root, reply, updates = writer(tmp_path)
    reply.publish = lambda update: updates.append((deepcopy(update), manager.get_messages('work')))
    prose = 'Before\n```json\n{"type":"confirm","content":"ordinary prose"}\n```'
    reply.text(prose)
    assert manager.get_messages('work')[0]['content'] == ''  # unpublished buffer
    reply.execution(root, 'research-1', 'activity', call())
    first_id = reply.reply['segments'][1]['id']
    reply.text('After')
    reply.execution(root, 'research-1', 'activity', call(status='completed', finished_at=3))
    reply.finish('completed')
    stored = manager.get_messages('work')
    assert len(stored) == 1 and stored[0]['id'] == reply.id
    assert [r['type'] for r in stored[0]['segments']] == ['text', 'execution', 'text']
    assert stored[0]['segments'][0]['text'] == prose
    assert stored[0]['segments'][1]['id'] == first_id
    assert stored[0]['segments'][1]['execution']['status'] == 'completed'
    for update, snapshot in updates[1:]:
        assert snapshot[0]['metadata']['revision'] == update['revision']
        assert snapshot[0]['metadata']['reply_id'] == update['reply_id']
    assert stored[0]['metadata']['chat_task_id'] == 'chat-task'


@pytest.mark.parametrize('terminal', ['completed', 'failed', 'cancelled', 'paused'])
def test_terminal_paths_finalize_same_message_and_separate_successive_turns(tmp_path, terminal):
    manager, root, reply, _ = writer(tmp_path)
    reply.execution(root, 'research-1', 'activity', call())
    reply.finish(terminal, 'visible terminal notice')
    reply.finish('failed', 'must not duplicate')
    second = ReplyWriter(manager, 'work', 'chat-task-2', root, lambda _: None)
    second.text('second answer'); second.finish('completed')
    messages = manager.get_messages('work')
    assert len(messages) == 2 and messages[0]['id'] != messages[1]['id']
    assert messages[0]['metadata']['status'] == terminal
    assert messages[0]['segments'][0]['execution']['status'] == 'interrupted'
    assert 'visible terminal notice' in messages[0]['content']
    assert 'must not duplicate' not in messages[0]['content']


def test_parallel_nested_and_model_calls_share_one_order_not_experiment_task_id(tmp_path):
    _, root, reply, _ = writer(tmp_path)
    reply.execution(root, 'research-1', 'activity', call('outer', experiment_id='E1', action_id='A1'))
    def nested(index):
        reply.execution(root, 'research-1', 'activity', call(f'child-{index}', parent_call_id='outer'))
    with ThreadPoolExecutor(max_workers=3) as pool: list(pool.map(nested, range(3)))
    reply.execution(root, 'research-1', 'model', {'sequence': 42, 'sent_at': 2, 'state': 'sent',
                                               'parent_call_id': 'outer', 'experiment_id': 'E1'})
    reply.finish('cancelled')
    segments = reply.reply['segments']
    assert len(segments) == 5 and len({s['id'] for s in segments}) == 5
    assert segments[-1]['ref']['id'] == 'model:42'
    assert segments[-1]['execution']['parent_call_id'] == 'outer'
    assert reply.reply['metadata']['chat_task_id'] == 'chat-task'


def test_exact_old_journal_resolution_and_safe_projection(tmp_path):
    manager, root, reply, _ = writer(tmp_path)
    (root / '.system').mkdir()
    (root / 'inputs').mkdir(); (root / 'inputs/data.csv').write_text('x\n1')
    item = call('old-call', inputs={'path': 'inputs/data.csv', 'prompt': 'SECRET'},
                output={'token': 'SECRET'}, purpose='SECRET', message='SECRET', host='SECRET')
    reply.execution(root, 'old-research', 'activity', item)
    sid = reply.reply['segments'][0]['id']
    reply.finish('completed')
    with sqlite3.connect(root / '.system/research.sqlite') as db:
        db.executescript('CREATE TABLE journal(research_id TEXT,seq INTEGER,kind TEXT,payload TEXT);'
                         'CREATE TABLE model_requests(research_id TEXT,sequence INTEGER,payload TEXT);')
        db.execute('INSERT INTO journal VALUES (?,?,?,?)', ('old-research', 1, 'activity_finished', json.dumps({**item, 'status':'failed', 'finished_at':4})))
        # No snapshot activity contains old-call; later events exceed all UI windows.
        db.executemany('INSERT INTO journal VALUES (?,?,?,?)', [('old-research', i, 'activity_finished', json.dumps(call(f'new-{i}'))) for i in range(2, 310)])
    result = resolve_executions(root, manager.get_messages('work')[0], [sid, 'unreferenced-id'])
    assert len(result) == 1 and result[0]['status'] == 'failed'
    assert result[0]['file'] == 'inputs/data.csv'
    assert 'SECRET' not in json.dumps(result)
    assert not {'inputs', 'output', 'purpose', 'host'} & result[0].keys()
    for path in ['../secret.txt', '/etc/passwd', '.system/research.sqlite', '.env']:
        assert safe_file(root, path) is None
    (root / 'escape.txt').symlink_to('/etc/passwd')
    assert safe_file(root, 'escape.txt') is None


def _append_history(base, prefix):
    manager = ChatHistoryManager(base)
    for i in range(12): manager.save_message('work', 'user', f'{prefix}-{i}')


def test_atomic_cross_process_history_updates_preserve_unrelated_messages(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    processes = [ctx.Process(target=_append_history, args=(str(tmp_path), i)) for i in range(3)]
    for process in processes: process.start()
    while any(process.is_alive() for process in processes):
        path = tmp_path / 'work/chat_history.json'
        if path.exists(): assert isinstance(json.loads(path.read_text())['messages'], list)
        for process in processes: process.join(.01)
    assert all(process.exitcode == 0 for process in processes)
    messages = ChatHistoryManager(str(tmp_path)).get_messages('work')
    assert len(messages) == len({r['content'] for r in messages}) == 36


def test_failed_atomic_replace_keeps_previous_file_and_corrupt_input_is_not_overwritten(tmp_path, monkeypatch):
    manager, _, reply, _ = writer(tmp_path)
    before = manager.get_messages('work')
    import services.chat_services.chat_history_manager as module
    monkeypatch.setattr(module.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('disk unavailable')))
    with pytest.raises(OSError): manager.save_message('work', 'user', 'not committed')
    assert manager.get_messages('work') == before
    assert not list((tmp_path / 'work').glob('.chat_history-*'))
    path = tmp_path / 'work/chat_history.json'; path.write_text('{corrupt')
    with pytest.raises(ValueError): manager.save_message('work', 'user', 'do not erase')
    assert path.read_text() == '{corrupt'


def test_restart_reconciliation_does_not_rewrite_newer_reply(tmp_path):
    manager, _, reply, _ = writer(tmp_path)
    stale = reply.reply['metadata']['revision']
    reply.text('durable'); reply.flush()
    assert manager.interrupt_reply('work', reply.id, stale) is None
    interrupted = manager.interrupt_reply('work', reply.id, reply.reply['metadata']['revision'])
    assert interrupted['metadata']['status'] == 'interrupted'
    assert interrupted['content'] == 'durable'


def test_reference_resolver_uses_existing_work_authorization(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from fastapi import HTTPException
    from routers.chat_routes import chat
    manager, root, reply, _ = writer(tmp_path)
    reads = []
    class Service:
        def __init__(self, db): pass
        def get_session_by_work_id(self, work_id, user_id): return None
        def get_work_chat_history(self, work_id):
            reads.append(work_id)
            return manager.get_messages(work_id)
    monkeypatch.setattr(chat, 'ChatService', Service)
    monkeypatch.setattr('services.data_services.crud.get_work', lambda *_: SimpleNamespace(created_by=7))
    monkeypatch.setattr(chat, 'get_workspace_path', lambda _: root)
    request = chat.ExecutionReferences(reply_id=reply.id, segment_ids=[])
    with pytest.raises(HTTPException) as denied:
        asyncio.run(chat.get_execution_references('work', request, 8, None))
    assert denied.value.status_code == 403 and not reads
    assert asyncio.run(chat.get_execution_references('work', request, 7, None)) == {'reply_id': reply.id, 'executions': []}


def test_live_replay_keeps_task_sequence_and_stable_reply(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from routers.chat_routes import chat
    async def scenario():
        task = SimpleNamespace(task_id='chat-task', reply_id='reply', action='ask')
        updates = [SimpleNamespace(type='reply_update', seq=i, data={'reply_id':'reply','revision':i,'segments':[]}) for i in range(1, 4)]
        monkeypatch.setattr(chat.task_manager, 'get_outputs_after', lambda _, seq: [u for u in updates if u.seq > seq])
        delivered = []
        class Socket:
            client_state = SimpleNamespace(value=1)
            async def send_text(self, raw): delivered.append(json.loads(raw))
        manager = chat.ConnectionManager()
        await manager.replay('work', Socket(), task, 1)
        assert [p['seq'] for p in delivered if 'seq' in p] == [2, 3]
        assert all(p.get('reply_id') == 'reply' for p in [delivered[0], delivered[-1]])
        assert delivered[1]['update']['revision'] == 2
        await manager.send('work', {'type':'reply_update','update':updates[-1].data}, 3)
        assert len(delivered) == 4, 'already-replayed output is not sent again'
    asyncio.run(scenario())


def test_operation_details_show_selected_inputs_and_concrete_sanitized_failure(tmp_path):
    from services.chat_services.conversation_timeline import execution_projection
    (tmp_path / 'data.csv').write_text('x\n1')
    read = execution_projection(tmp_path, 'research', 'activity', call(inputs={'path':'data.csv','start':10,'limit':200}))
    assert read['label'].endswith('data.csv') and '10' in read['detail'] and '200' in read['detail']
    run = tmp_path / 'runs/run-1'; run.mkdir(parents=True)
    (run / 'input.py').write_text('import missing_package')
    (run / 'stderr.log').write_text("ModuleNotFoundError: No module named 'missing_package'")
    (tmp_path / 'manifest.json').write_text(json.dumps({'runs':{'run-1':{
        'input':'runs/run-1/input.py','stderr':'runs/run-1/stderr.log'}}}))
    failed = execution_projection(tmp_path, 'research', 'activity', call('python', capability='run_python',
        status='failed', error_type='PythonExecutionError', run_id='run-1',
        message="Traceback (most recent call last):\n  secret = 'hidden-source-literal'\nModuleNotFoundError: No module named 'missing_package'",
        inputs={'code':"secret = 'hidden-source-literal'\nimport missing_package"}))
    assert '2 行' in failed['detail']
    assert failed['message'] == "ModuleNotFoundError: No module named 'missing_package'"
    assert failed['script_file'] == 'runs/run-1/input.py' and failed['error_file'] == 'runs/run-1/stderr.log'
    assert 'hidden-source-literal' not in json.dumps(failed)


def test_failure_detail_redacts_credentials_provider_urls_and_external_paths(tmp_path):
    from services.chat_services.conversation_timeline import safe_diagnostic
    diagnostic = safe_diagnostic(tmp_path,
        'ValueError: shape mismatch; api_key="owner-secret"; Authorization: Bearer bearer-secret; '
        'https://owner:secret@example.test/request?key=private; /home/owner/private/config.json')
    assert 'shape mismatch' in diagnostic
    for secret in ['owner-secret','bearer-secret','owner:secret','example.test','/home/owner']:
        assert secret not in diagnostic
    assert '内部请求数据' in safe_diagnostic(tmp_path, 'messages: [{"role":"system","content":"private prompt"}]')


def test_source_projection_retains_only_known_semantic_sources_and_safe_files(tmp_path):
    from services.chat_services.conversation_timeline import public_block
    (tmp_path / 'data.csv').write_text('x\n1')
    block = public_block(tmp_path, {'type':'research_context','content':{
        'files':['data.csv','research:feedback','research:consistency','research:private','.system/research.sqlite','../private.json'],
        'result_ids':['R1','invalid-secret']}})
    assert block['content']['files'] == ['data.csv','research:feedback','research:consistency']
    assert block['content']['result_ids'] == ['R1']


def test_progress_projection_uses_recorded_steps_and_preserves_stage_notes(tmp_path):
    from services.chat_services.conversation_timeline import execution_projection, public_block
    experience = execution_projection(tmp_path, 'research', 'activity', call(
        capability='experience.candidates', output={'candidates':3}))
    assert experience['label'] == '更新经验索引' and '3 项' in experience['detail']
    model = execution_projection(tmp_path, 'research', 'model', {
        'sequence':1, 'step':'撰写论文', 'state':'sent', 'sent_at':1,
        'purpose':'inherited old plan'})
    assert model['label'] == '撰写论文' and model['detail'] == ''
    assert 'inherited old plan' not in json.dumps(model)
    phase = public_block(tmp_path, {'type':'paper_phase','content':{
        'stage':'export','status':'in_progress','note':'编译 PDF','inputs':'private'}})
    assert phase['content'] == {'stage':'export','status':'in_progress','note':'编译 PDF'}
    refs = execution_projection(tmp_path, 'research', 'activity', call(
        capability='record_results', output={'artifact_refs':['R1','R2'],'rejected':1}))
    assert refs['result_ids'] == ['R1','R2'] and '2 项结果' in refs['detail'] and '1 项未通过' in refs['detail']
