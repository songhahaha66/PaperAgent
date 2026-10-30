"""Process-local workspace subscriptions, independent of agent execution.

Each socket owns its replay cursor and FIFO writer. Network backpressure never
holds the workspace lock or blocks a task/another browser. Durable replies and
research snapshots remain the recovery authority after process loss.
"""
import asyncio
import json
import logging
from weakref import WeakValueDictionary
from dataclasses import dataclass

from .task_manager import task_manager

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class Subscription:
    socket: object
    queue: asyncio.Queue
    task_id: str | None = None
    cursor: int = 0
    writer: asyncio.Task | None = None


class ConnectionManager:
    def __init__(self, queue_size=256, send_timeout=10):
        self.active_connections: dict[str, dict[object, Subscription]] = {}
        self._locks = WeakValueDictionary()
        self.queue_size = queue_size
        self.send_timeout = send_timeout

    def lock(self, work_id):
        return self._locks.setdefault(work_id, asyncio.Lock())

    def register(self, work_id, websocket, cursor=0, task_id=None):
        self.disconnect(work_id, websocket)
        subscription = Subscription(websocket, asyncio.Queue(self.queue_size), task_id, cursor)
        self.active_connections.setdefault(work_id, {})[websocket] = subscription
        subscription.writer = asyncio.create_task(self._write(work_id, subscription))
        return subscription

    def disconnect(self, work_id, websocket):
        connections = self.active_connections.get(work_id, {})
        subscription = connections.pop(websocket, None)
        if subscription and subscription.writer is not asyncio.current_task():
            subscription.writer.cancel()
        if not connections:
            self.active_connections.pop(work_id, None)

    async def _write(self, work_id, subscription):
        try:
            while True:
                batch = await subscription.queue.get()
                try:
                    for payload in batch:
                        task_id, seq = payload.get('task_id'), payload.get('seq')
                        if task_id and task_id != subscription.task_id:
                            subscription.task_id, subscription.cursor = task_id, 0
                        if seq is not None and seq <= subscription.cursor:
                            continue
                        async with asyncio.timeout(self.send_timeout):
                            await subscription.socket.send_text(json.dumps(payload, ensure_ascii=False))
                        if seq is not None:
                            subscription.cursor = seq
                finally:
                    subscription.queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.info('Workspace subscriber disconnected: %s', work_id)
            self.disconnect(work_id, subscription.socket)
            await self._close(subscription.socket)

    async def _close(self, websocket):
        try:
            await asyncio.wait_for(websocket.close(code=1013), self.send_timeout)
        except Exception:
            pass

    def _offer(self, work_id, subscription, batch):
        try:
            subscription.queue.put_nowait(batch)
        except asyncio.QueueFull:
            # Force reconnect + replay instead of silently dropping an event.
            self.disconnect(work_id, subscription.socket)
            asyncio.create_task(self._close(subscription.socket))

    async def send_to(self, work_id, websocket, payload):
        """Connection-local acknowledgements/errors must never be broadcast."""
        subscription = self.active_connections.get(work_id, {}).get(websocket)
        if subscription:
            self._offer(work_id, subscription, [payload])

    async def send(self, work_id, payload, seq=None):
        async with self.lock(work_id):
            task = task_manager.get_task(work_id)
            payload = {**payload}
            if task:
                payload.setdefault('task_id', task.task_id)
            if seq is not None:
                payload['seq'] = seq
            for subscription in list(self.active_connections.get(work_id, {}).values()):
                self._offer(work_id, subscription, [payload])

    async def replay(self, work_id, websocket, task, after_seq):
        async with self.lock(work_id):
            # A future/invalid cursor cannot suppress subsequent live events.
            last_seq = getattr(task, 'last_seq', None)
            if type(after_seq) is not int or after_seq < 0 or (last_seq is not None and after_seq > last_seq):
                after_seq = 0
            subscription = self.active_connections.get(work_id, {}).get(websocket)
            if subscription is None:
                subscription = self.register(work_id, websocket, after_seq, task.task_id)
            else:
                subscription.task_id, subscription.cursor = task.task_id, after_seq
            identity = {'task_id': task.task_id, 'reply_id': task.reply_id, 'action': task.action,
                        'request_id': getattr(task, 'request_id', None),
                        'after_seq': after_seq,
                        'started_at': getattr(task, 'started_at', None)}
            batch = [{'type': 'reconnect', **identity}]
            for output in task_manager.get_outputs_after(work_id, after_seq):
                key = 'update' if output.type == 'reply_update' else 'content' if output.type == 'content' else 'block'
                batch.append({'type': output.type, key: output.data, 'seq': output.seq, 'task_id': task.task_id})
            batch.append({'type': 'reconnect_complete', **identity})
            # Replay is one bounded queue entry referring to existing task output;
            # live events queue behind it without stalling other subscribers.
            self._offer(work_id, subscription, batch)

    def is_connected(self, work_id):
        return bool(self.active_connections.get(work_id))

    def get_connection_count(self):
        return sum(len(connections) for connections in self.active_connections.values())
