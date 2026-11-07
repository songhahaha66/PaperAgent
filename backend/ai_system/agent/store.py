"""Atomic Item snapshots and ordered events. No research/activity or chat-file fallback."""
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import time
from services.file_services.workspace_layout import workspace_path
from .protocol import ITEM, AgentEvent, Thread, Turn

DB_PATH = '.system/agent.sqlite'


class AgentStore:
    def __init__(self, root, *, read_only=False):
        self.root = Path(root).resolve()
        self.path = workspace_path(self.root, DB_PATH)
        self.read_only = read_only
        if read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY, research_id TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, root_turn_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS turns_root ON turns(root_turn_id);
                CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, updated_at REAL NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS items_turn ON items(turn_id, sequence);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL,
                    turn_id TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tool_receipts(thread_id TEXT NOT NULL, call_id TEXT NOT NULL,
                    signature TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(thread_id, call_id));
                CREATE TABLE IF NOT EXISTS tool_calls(thread_id TEXT NOT NULL, call_id TEXT NOT NULL,
                    signature TEXT NOT NULL, PRIMARY KEY(thread_id, call_id));
            ''')

    def connect(self):
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=30) if self.read_only else sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def transaction(self):
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _event(db, method, thread_id, turn_id=None, **payload):
        event = AgentEvent(seq=0, method=method, thread_id=thread_id, turn_id=turn_id, **payload)
        cursor = db.execute('INSERT INTO events(thread_id,turn_id,payload) VALUES (?,?,?)',
                            (thread_id, turn_id, event.model_dump_json(exclude_none=True)))
        event.seq = cursor.lastrowid
        db.execute('UPDATE events SET payload=? WHERE seq=?', (event.model_dump_json(exclude_none=True), event.seq))
        return event.model_dump(mode='json', exclude_none=True)

    def start_thread(self, thread: Thread):
        with self.transaction() as db:
            row = db.execute('SELECT payload FROM threads WHERE id=?', (thread.id,)).fetchone()
            if row:
                existing = Thread.model_validate_json(row[0])
                if (existing.research_id, existing.parent_thread_id) != (thread.research_id, thread.parent_thread_id):
                    raise ValueError('Thread identity belongs to a different session')
                return None
            db.execute('INSERT INTO threads VALUES (?,?,?)', (thread.id, thread.research_id, thread.model_dump_json()))
            return self._event(db, 'thread/started', thread.id, thread=thread)

    def start_turn(self, turn: Turn):
        with self.transaction() as db:
            row = db.execute('SELECT payload FROM turns WHERE id=?', (turn.id,)).fetchone()
            if row:
                existing = Turn.model_validate_json(row[0])
                if existing.thread_id != turn.thread_id or existing.root_turn_id != turn.root_turn_id:
                    raise ValueError('Turn identity belongs to a different thread')
                return None
            db.execute('INSERT INTO turns VALUES (?,?,?,?)', (turn.id, turn.thread_id, turn.root_turn_id, turn.model_dump_json()))
            return self._event(db, 'turn/started', turn.thread_id, turn.id, turn=turn)

    def put_item(self, value, method, *, receipt=None):
        item = ITEM.validate_python(value)
        with self.transaction() as db:
            turn_row = db.execute('SELECT payload FROM turns WHERE id=?', (item.turn_id,)).fetchone()
            if not turn_row or Turn.model_validate_json(turn_row[0]).status != 'running':
                raise ValueError('Cannot publish an Item outside a running Turn')
            if Turn.model_validate_json(turn_row[0]).thread_id != item.thread_id:
                raise ValueError('Item thread must match its Turn')
            old = db.execute('SELECT payload,sequence FROM items WHERE id=?', (item.id,)).fetchone()
            if old:
                previous = ITEM.validate_json(old[0])
                if (previous.thread_id, previous.turn_id, previous.type) != (item.thread_id, item.turn_id, item.type):
                    raise ValueError('Item identity is immutable')
                if previous.status != 'running':
                    return None  # Terminal snapshots never regress on duplicate callbacks.
                if method == 'item/started':
                    return None
                item.sequence = old[1]
            else:
                if method != 'item/started':
                    raise ValueError('Item must start before it can update or complete')
                item.sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM items WHERE turn_id=?', (item.turn_id,)).fetchone()[0]
            if (method == 'item/completed') != (item.status != 'running'):
                raise ValueError('Item lifecycle event disagrees with status')
            item.updated_at = time.time()
            db.execute('INSERT INTO items VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,payload=excluded.payload',
                       (item.id, item.thread_id, item.turn_id, item.sequence, time.time(), item.model_dump_json(exclude_none=True)))
            if receipt:
                call_id, signature, result = receipt
                db.execute('INSERT INTO tool_receipts VALUES (?,?,?,?)', (item.thread_id, call_id, signature, json.dumps(result, ensure_ascii=False)))
            return self._event(db, method, item.thread_id, item.turn_id, item=item)

    def finish_turn(self, turn_id, status):
        if status not in {'completed','failed','interrupted'}:raise ValueError('Invalid terminal Turn status')
        with self.transaction() as db:
            row = db.execute('SELECT payload FROM turns WHERE id=?', (turn_id,)).fetchone()
            if not row:
                return None
            turn = Turn.model_validate_json(row[0])
            if turn.status != 'running':
                return None
            if db.execute("SELECT 1 FROM items WHERE turn_id=? AND json_extract(payload,'$.status')='running'", (turn_id,)).fetchone():
                raise ValueError('Complete running Items before completing their Turn')
            turn.status, turn.finished_at = status, time.time()
            db.execute('UPDATE turns SET payload=? WHERE id=?', (turn.model_dump_json(), turn.id))
            return self._event(db, 'turn/completed', turn.thread_id, turn.id, turn=turn)

    def get_item(self, identity):
        if not self.path.exists():return None
        with closing(self.connect()) as db:
            row = db.execute('SELECT payload FROM items WHERE id=?', (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def receipt(self, thread_id, call_id, signature):
        with closing(self.connect()) as db:
            row = db.execute('SELECT signature,result FROM tool_receipts WHERE thread_id=? AND call_id=?', (thread_id, call_id)).fetchone()
        if row and row[0] != signature:
            raise ValueError('同一工具调用不能更改参数，请发起新调用')
        return json.loads(row[1]) if row else None

    def bind_call(self, thread_id, call_id, signature):
        # Bind before executing, including calls interrupted before their receipt.
        with self.transaction() as db:
            row = db.execute('SELECT signature FROM tool_calls WHERE thread_id=? AND call_id=?', (thread_id,call_id)).fetchone()
            if row and row[0] != signature:
                raise ValueError('同一工具调用不能更改参数，请发起新调用')
            db.execute('INSERT OR IGNORE INTO tool_calls VALUES (?,?,?)', (thread_id,call_id,signature))

    def items(self, *, turn_id=None, root_turn_id=None, research_id=None, limit=240):
        if not self.path.exists():return []
        where, params = [], []
        for column, value in [('i.turn_id',turn_id), ('t.root_turn_id',root_turn_id)]:
            if value is not None:where.append(column+'=?');params.append(value)
        if research_id is not None:
            where.append("(th.research_id=? OR json_extract(i.payload,'$.research_id')=?)")
            params.extend([research_id,research_id])
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        with closing(self.connect()) as db:
            rows = db.execute('SELECT i.payload FROM items i JOIN turns t ON t.id=i.turn_id JOIN threads th ON th.id=i.thread_id'+clause+
                              ' ORDER BY i.rowid DESC LIMIT ?', (*params, limit)).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def events(self, root_turn_id, after=0):
        if not self.path.exists():return []
        with closing(self.connect()) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT e.payload FROM events e JOIN turns t ON t.id=e.turn_id WHERE t.root_turn_id=? AND e.seq>? ORDER BY e.seq', (root_turn_id, after))]

    def interrupt(self, *, root_turn_id=None, research_id=None):
        """Coordinator-only reconciliation after ownership of the previous run ends."""
        events = []
        for item in self.items(root_turn_id=root_turn_id, research_id=research_id, limit=100000):
            if item['status'] == 'running':
                item.update(status='interrupted', finished_at=time.time())
                event = self.put_item(item, 'item/completed')
                if event:events.append(event)
        with closing(self.connect()) as db:
            rows = db.execute('SELECT t.payload FROM turns t JOIN threads th ON th.id=t.thread_id WHERE '+
                              ('t.root_turn_id=?' if root_turn_id else "(th.research_id=? OR t.id IN (SELECT turn_id FROM items WHERE json_extract(payload,'$.research_id')=?))"),
                              (root_turn_id,) if root_turn_id else (research_id,research_id)).fetchall()
        for row in rows:
            turn = Turn.model_validate_json(row[0])
            if turn.status == 'running':
                event = self.finish_turn(turn.id, 'interrupted')
                if event:events.append(event)
        return events
