"""Fan-out, replay ordering and backpressure without real model/network calls."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.chat_services.connections import ConnectionManager
from services.chat_services.task_manager import task_manager, TaskStatus


class Socket:
    def __init__(self, gate=None, fail=False):
        self.events = []
        self.gate = gate
        self.fail = fail
        self.closed = None

    async def send_text(self, raw):
        if self.gate:
            await self.gate.wait()
        if self.fail:
            raise OSError('disconnected')
        self.events.append(json.loads(raw))

    async def close(self, code):
        self.closed = code


async def drain(manager, work='work'):
    await asyncio.wait_for(asyncio.gather(*[
        sub.queue.join() for sub in manager.active_connections.get(work, {}).values()
    ]), 1)


def test_independent_subscribers_replay_new_turn_and_disconnect(monkeypatch):
    async def scenario():
        task = SimpleNamespace(task_id='first', reply_id='reply', action='run', last_seq=3)
        monkeypatch.setattr(task_manager, 'get_task', lambda _: task)
        outputs = [SimpleNamespace(type='content', data=str(i), seq=i) for i in range(1, 4)]
        monkeypatch.setattr(task_manager, 'get_outputs_after', lambda _, seq: [o for o in outputs if o.seq > seq])
        manager = ConnectionManager()
        a, b, c, unrelated = Socket(), Socket(), Socket(), Socket()
        manager.register('other', unrelated)
        await manager.replay('work', a, task, 0)
        await drain(manager)
        await manager.replay('work', b, task, 2)
        await manager.send('work', {'type': 'content', 'content': '3'}, 3)
        await manager.send('work', {'type': 'content', 'content': '4'}, 4)
        await drain(manager)
        assert [e['seq'] for e in a.events if 'seq' in e] == [1, 2, 3, 4]
        assert [e['seq'] for e in b.events if 'seq' in e] == [3, 4]
        assert not unrelated.events
        assert manager.get_connection_count() == 3
        manager.disconnect('work', a)
        await manager.send_to('work', b, {'type': 'error', 'scope': 'command'})
        await drain(manager)
        assert b.events[-1]['scope'] == 'command'
        task = SimpleNamespace(task_id='second', reply_id='new', action='ask', last_seq=1)
        await manager.send('work', {'type': 'start'})
        await manager.send('work', {'type': 'content', 'content': 'new'}, 1)
        await drain(manager)
        assert b.events[-1]['seq'] == 1 and b.events[-1]['task_id'] == 'second'
        # Invalid cursor is reset rather than suppressing all future output.
        await manager.replay('work', c, task, 999)
        await drain(manager)
        assert [e['seq'] for e in c.events if 'seq' in e] == [1, 2, 3]
        for work, socket in [('work', b), ('work', c), ('other', unrelated)]:
            manager.disconnect(work, socket)
        assert manager.get_connection_count() == 0
    asyncio.run(scenario())


def test_slow_and_broken_subscribers_do_not_block_fast_browser_or_producer():
    async def scenario():
        gate = asyncio.Event()
        manager = ConnectionManager(queue_size=2, send_timeout=0.05)
        slow, broken, fast = Socket(gate), Socket(fail=True), Socket()
        manager.register('work', slow)
        manager.register('work', broken)
        fast_sub = manager.register('work', fast)
        for i in range(6):
            await manager.send('work', {'type': 'content', 'content': str(i)})
            await asyncio.wait_for(fast_sub.queue.join(), 0.1)
        assert len(fast.events) == 6
        assert slow not in manager.active_connections['work']
        assert broken not in manager.active_connections['work']
        await asyncio.sleep(0)
        manager.disconnect('work', fast)
    asyncio.run(scenario())


def test_send_timeout_evicts_only_the_stalled_socket():
    async def scenario():
        manager = ConnectionManager(send_timeout=0.01)
        slow, fast = Socket(asyncio.Event()), Socket()
        slow_sub = manager.register('work', slow)
        fast_sub = manager.register('work', fast)
        await manager.send('work', {'type': 'complete'})
        await asyncio.wait_for(fast_sub.queue.join(), 0.1)
        await asyncio.wait_for(slow_sub.writer, 0.2)
        assert fast.events[-1]['type'] == 'complete' and slow.closed == 1013
        assert manager.get_connection_count() == 1
        manager.disconnect('work', fast)
    asyncio.run(scenario())


def test_task_reservation_covers_pending_and_terminal_finalization(monkeypatch):
    import pytest
    async def scenario():
        monkeypatch.setattr(task_manager, '_tasks', {})
        old = task_manager.create_task('work', 7, 'first')
        with pytest.raises(ValueError): task_manager.create_task('work', 7, 'second', action='ask')
        assert task_manager.get_task('work') is old and old.status == TaskStatus.PENDING
        old.status = TaskStatus.CANCELLED
        gate = asyncio.Event()
        old._async_task = asyncio.create_task(gate.wait())
        with pytest.raises(ValueError): task_manager.create_task('work', 7, 'second')
        gate.set()
        await old._async_task
        new = task_manager.create_task('work', 7, 'second')
        new.status = TaskStatus.COMPLETED
        monkeypatch.setattr(task_manager, '_completed_retention', 0)
        await task_manager._cleanup_completed_task('work', old.task_id)
        assert task_manager.get_task('work') is new
        await task_manager._cleanup_completed_task('work', new.task_id)
        assert task_manager.get_task('work') is None
    asyncio.run(scenario())
