"""Thread/Turn ownership follows async context, not the Python call stack."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from hashlib import sha256
import time
from uuid import uuid4
from .protocol import Thread, Turn
from .store import AgentStore

CURRENT_TURN = ContextVar('agent_turn', default=None)
CURRENT_TOOL = ContextVar('agent_tool', default=None)


@dataclass
class TurnContext:
    store: AgentStore
    thread_id: str
    turn_id: str
    root_turn_id: str
    publish: object = None
    lock: object = field(default_factory=RLock)

    def emit(self, event):
        # The store has committed before a transport sees the event.
        if event and self.publish:
            self.publish(event)

    def put(self, item, method, **kwargs):
        with self.lock:
            event = self.store.put_item(item, method, **kwargs)
            self.emit(event)
            return event

    def finish(self, status):
        with self.lock:
            for item in self.store.items(turn_id=self.turn_id, limit=100000):
                if item['status'] == 'running':
                    item.update(status='interrupted', finished_at=time.time())
                    self.put(item, 'item/completed')
            self.emit(self.store.finish_turn(self.turn_id, status))


def new_turn(root, thread_id, *, turn_id=None, root_turn_id=None, research_id=None,
             parent_thread_id=None, parent_item_id=None, publish=None, lock=None):
    store = AgentStore(root)
    turn_id = turn_id or uuid4().hex
    context = TurnContext(store, thread_id, turn_id, root_turn_id or turn_id, publish)
    if lock is not None:context.lock = lock
    with context.lock:
        context.emit(store.start_thread(Thread(id=thread_id, research_id=research_id,
                                              parent_thread_id=parent_thread_id, parent_item_id=parent_item_id, created_at=time.time())))
        context.emit(store.start_turn(Turn(id=turn_id, thread_id=thread_id, root_turn_id=context.root_turn_id, started_at=time.time())))
    return context


@contextmanager
def session_turn(ctx, thread_id):
    """Child agents own a separate thread and turn; helpers never create a thread."""
    parent = CURRENT_TURN.get()
    if parent and parent.thread_id == thread_id:
        yield parent
        return
    tool = CURRENT_TOOL.get()
    research = getattr(ctx, 'research', None)
    root = research.root if research else ctx.workspace
    child = new_turn(root, thread_id, research_id=research.research_id if research else None,
                     root_turn_id=parent.root_turn_id if parent else None,
                     parent_thread_id=parent.thread_id if parent else None,
                     parent_item_id=tool.item['id'] if tool else None, publish=parent.publish if parent else None,
                     lock=parent.lock if parent else None)
    if tool:
        tool.update(child_thread_id=thread_id, child_turn_id=child.turn_id)
    token = CURRENT_TURN.set(child)
    owner = CURRENT_TOOL.set(None)
    try:
        yield child
    except BaseException as exc:
        child.finish('interrupted' if type(exc).__name__ == 'CancelledError' else 'failed')
        raise
    else:
        child.finish('completed')
    finally:
        CURRENT_TOOL.reset(owner)
        CURRENT_TURN.reset(token)


def item_id(thread_id, call_id):
    return sha256((thread_id + '\0' + call_id).encode()).hexdigest()
