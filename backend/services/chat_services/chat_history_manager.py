"""Ordered conversation messages in the workspace database.

Legacy JSON is imported once by the workspace migration. Each reply update
commits one message; concurrent writers never read/replace the whole history.
"""
import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from config.paths import get_workspaces_path


def default_history(work_id):
    return {'work_id': work_id, 'session_id': f'{work_id}_session', 'messages': [],
            'context': {'current_topic': '', 'generated_files': [], 'workflow_state': 'created'},
            'created_at': datetime.now().isoformat(), 'version': '2.0'}


def migrate_chat(db, root):
    """Called inside the workspace cutover transaction; retain original bytes."""
    db.execute('CREATE TABLE IF NOT EXISTS chat_metadata (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS chat_messages (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)')
    if db.execute('SELECT 1 FROM chat_metadata WHERE id=1').fetchone():
        return
    history = default_history(root.name)
    source = root / 'chat_history.json'
    if source.is_file():
        if not source.resolve().is_relative_to(root.resolve()):
            raise ValueError('聊天历史必须位于工作区内')
        raw = source.read_bytes()
        imported = json.loads(raw)
        if isinstance(imported, list): imported = {'messages': imported}
        if not isinstance(imported, dict) or not isinstance(imported.get('messages', []), list):
            raise ValueError('聊天历史格式无效')
        history.update(imported)
        db.execute('INSERT OR IGNORE INTO legacy_records VALUES (?,?)', ('chat_history.json', raw))
    for message in history.pop('messages'):
        message.setdefault('id', uuid.uuid4().hex)
        db.execute('INSERT INTO chat_messages(id,payload) VALUES (?,?)',
                   (str(message['id']), json.dumps(message, ensure_ascii=False)))
    db.execute('INSERT INTO chat_metadata VALUES (1,?)', (json.dumps(history, ensure_ascii=False),))


class ChatHistoryManager:
    def __init__(self, workspace_base=None):
        self.workspace_base = str(workspace_base or get_workspaces_path())

    @contextmanager
    def _database(self, work_id, *, write=False):
        from ai_system.research.store import ResearchStore
        root = (Path(self.workspace_base) / work_id).resolve()
        if not root.is_relative_to(Path(self.workspace_base).resolve()):
            raise ValueError('聊天工作区路径无效')
        store = ResearchStore(root, 'workspace-chat')
        db = store._connect()
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get_work_history(self, work_id):
        with self._database(work_id) as db:
            history = json.loads(db.execute('SELECT payload FROM chat_metadata WHERE id=1').fetchone()[0])
            history['messages'] = [json.loads(row[0]) for row in db.execute('SELECT payload FROM chat_messages ORDER BY sequence')]
            return history

    def save_message(self, work_id, role, content, metadata=None):
        message = {'id': uuid.uuid4().hex, 'role': role, 'content': content,
                   'timestamp': datetime.now().isoformat(), 'metadata': metadata or {},
                   'json_blocks': [], 'message_type': 'text'}
        with self._database(work_id, write=True) as db:
            self._write_message(db, message)

    @staticmethod
    def _write_message(db, message):
        db.execute('INSERT INTO chat_messages(id,payload) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                   (str(message['id']), json.dumps(message, ensure_ascii=False)))

    def update_context(self, work_id, context_updates):
        with self._database(work_id, write=True) as db:
            history = json.loads(db.execute('SELECT payload FROM chat_metadata WHERE id=1').fetchone()[0])
            history.setdefault('context', {}).update(context_updates)
            db.execute('UPDATE chat_metadata SET payload=? WHERE id=1', (json.dumps(history, ensure_ascii=False),))

    def get_messages(self, work_id, limit=None):
        messages = self.get_work_history(work_id)['messages']
        return messages[-limit:] if limit else messages

    def clear_history(self, work_id):
        with self._database(work_id, write=True) as db:
            db.execute('DELETE FROM chat_messages')
            history = default_history(work_id)
            history.pop('messages')
            db.execute('UPDATE chat_metadata SET payload=? WHERE id=1', (json.dumps(history, ensure_ascii=False),))

    def upsert_reply(self, work_id, reply):
        with self._database(work_id, write=True) as db:
            row = db.execute('SELECT payload FROM chat_messages WHERE id=?', (str(reply['id']),)).fetchone()
            if row and json.loads(row[0]).get('metadata', {}).get('revision', 0) >= reply['metadata']['revision']:
                return
            self._write_message(db, reply)

    def interrupt_reply(self, work_id, reply_id, expected_revision):
        with self._database(work_id, write=True) as db:
            row = db.execute('SELECT payload FROM chat_messages WHERE id=?', (str(reply_id),)).fetchone()
            if row is None: return None
            reply = json.loads(row[0])
            metadata = reply.get('metadata', {})
            if metadata.get('status') != 'running' or metadata.get('revision') != expected_revision:
                return None
            metadata.update(status='interrupted', revision=expected_revision + 1)
            for segment in reply.get('segments', []):
                item = segment.get('execution')
                if item and not item.get('finished_at'): item['status'] = 'interrupted'
            self._write_message(db, reply)
            return reply
