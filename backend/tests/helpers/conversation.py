"""Conversation timeline builders shared by offline tests."""
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
