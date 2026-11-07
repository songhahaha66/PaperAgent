"""Typed conversation fixtures; production Items are created by ToolRuntime."""
from services.chat_services.chat_history_manager import ChatHistoryManager
from services.chat_services.conversation_timeline import TurnWriter
from ai_system.agent.protocol import ToolItem


def writer(tmp_path, publish=None):
    manager = ChatHistoryManager(str(tmp_path))
    root = tmp_path / 'work'
    updates = []
    reply = TurnWriter(manager, 'work', 'chat-task', root, publish or updates.append)
    return manager, root, reply, updates


def call(identity='call-1', **values):
    return {'id':identity,'tool_name':'read_file','type':'toolCall','call_id':identity,
            'label':'读取已有文件','started_at':1,'status':'running',**values}


def record(reply, values):
    item = ToolItem(**{**values,'thread_id':reply.turn.thread_id,'turn_id':reply.turn.turn_id}).model_dump(exclude_none=True)
    reply.turn.put(item, 'item/started' if item['status']=='running' else 'item/completed')
