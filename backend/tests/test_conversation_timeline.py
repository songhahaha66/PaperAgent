"""Offline acceptance cases for ordered durable conversation replies.

Authored for parent review; no model, server or browser is required.
"""
import json
import multiprocessing
import sys
from copy import deepcopy
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.chat_services.chat_history_manager import ChatHistoryManager
from services.chat_services.conversation_timeline import ReplyWriter


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


def _append_history(base, prefix):
    manager = ChatHistoryManager(base)
    for i in range(12): manager.save_message('work', 'user', f'{prefix}-{i}')


def test_atomic_cross_process_history_updates_preserve_unrelated_messages(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    processes = [ctx.Process(target=_append_history, args=(str(tmp_path), i)) for i in range(3)]
    for process in processes: process.start()
    while any(process.is_alive() for process in processes):
        assert isinstance(ChatHistoryManager(str(tmp_path)).get_messages('work'), list)
        for process in processes: process.join(.01)
    assert all(process.exitcode == 0 for process in processes)
    messages = ChatHistoryManager(str(tmp_path)).get_messages('work')
    assert len(messages) == len({r['content'] for r in messages}) == 36


def test_failed_transaction_keeps_previous_messages_and_corrupt_legacy_is_not_overwritten(tmp_path, monkeypatch):
    manager, _, reply, _ = writer(tmp_path)
    before = manager.get_messages('work')
    original = manager._write_message
    def fail_after_write(db, message):
        original(db, message)
        raise OSError('disk unavailable')
    monkeypatch.setattr(manager, '_write_message', fail_after_write)
    with pytest.raises(OSError): manager.save_message('work', 'user', 'not committed')
    assert manager.get_messages('work') == before
    root = tmp_path / 'legacy'; root.mkdir()
    path = root / 'chat_history.json'; path.write_text('{corrupt')
    with pytest.raises(ValueError): manager.save_message('legacy', 'user', 'do not erase')
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


def test_failure_detail_redacts_credentials_provider_urls_and_external_paths(tmp_path):
    from services.chat_services.conversation_timeline import safe_diagnostic
    diagnostic = safe_diagnostic(tmp_path,
        'ValueError: shape mismatch; api_key="owner-secret"; Authorization: Bearer bearer-secret; '
        'https://owner:secret@example.test/request?key=private; /home/owner/private/config.json')
    assert 'shape mismatch' in diagnostic
    for secret in ['owner-secret','bearer-secret','owner:secret','example.test','/home/owner']:
        assert secret not in diagnostic
    assert '内部请求数据' in safe_diagnostic(tmp_path, 'messages: [{"role":"system","content":"private prompt"}]')
