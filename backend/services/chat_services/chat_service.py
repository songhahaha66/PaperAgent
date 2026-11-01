"""
简化版聊天记录管理服务
使用JSON文件存储聊天记录，数据库只存储会话元数据
支持JSON卡片格式的聊天记录
"""

import logging
from typing import List, Optional, Dict
from sqlalchemy.orm import Session

from models.models import ChatSession
from services.chat_services.chat_history_manager import ChatHistoryManager

logger = logging.getLogger(__name__)


class ChatService:
    """简化版聊天服务"""

    def __init__(self, db_session: Session):
        self.db_session = db_session
        self.history_manager = ChatHistoryManager()

    def create_or_get_work_session(self, work_id: str, user_id: int) -> ChatSession:
        """为work创建或获取唯一的session（一个work对应一个session）"""
        try:
            # 查找现有session
            existing_session = self.db_session.query(ChatSession)\
                .filter(ChatSession.work_id == work_id)\
                .filter(ChatSession.created_by == user_id)\
                .first()

            if existing_session:
                logger.info(f"找到现有会话: {existing_session.session_id}")
                return existing_session

            # 创建新session
            session_id = f"{work_id}_main_session"
            session = ChatSession(
                session_id=session_id,
                work_id=work_id,
                system_type="brain",  # 统一使用主会话类型
                title="主会话",
                created_by=user_id
            )

            self.db_session.add(session)
            self.db_session.commit()
            self.db_session.refresh(session)

            logger.info(f"创建新会话: {session_id}")
            return session

        except Exception as e:
            logger.error(f"创建/获取会话失败: {e}")
            self.db_session.rollback()
            raise


    def get_work_chat_history(self, work_id: str, limit: Optional[int] = None) -> List[Dict]:
        """获取work的聊天记录（原始格式）"""
        try:
            messages = self.history_manager.get_messages(work_id, limit)
            from .conversation_timeline import public_reply
            messages = [public_reply(message) for message in messages]
            logger.info(f"获取聊天记录: {work_id}, 数量: {len(messages)}")
            return messages
        except Exception as e:
            logger.error(f"获取聊天记录失败: {e}")
            return []

    def get_work_chat_history_for_frontend(self, work_id: str, limit: Optional[int] = None) -> List[Dict]:
        """Merge persisted comment views for display only, never research context."""
        from datetime import datetime
        from pathlib import Path
        from .paper_history import chat_history
        messages=self.get_work_chat_history(work_id)
        from .task_manager import task_manager
        task = task_manager.get_task(work_id)
        for index, reply in enumerate(messages):
            metadata = reply.get('metadata', {})
            if metadata.get('reply_id') and metadata.get('status') == 'running':
                active = task and task.task_id == metadata.get('chat_task_id') and not task_manager._is_stale(task) and (task.status.value in {'pending', 'running'} or task._async_task is not None)
                if not active:
                    recovered = self.history_manager.interrupt_reply(work_id, reply['id'], metadata['revision'])
                    if recovered:
                        from .conversation_timeline import public_reply
                        messages[index] = public_reply(recovered)
        from .conversation_timeline import public_reply
        root=Path(self.history_manager.workspace_base)/work_id
        messages=[public_reply(message,root=root) for message in messages]+chat_history(root)
        messages.sort(key=lambda m:(datetime.fromisoformat(m['timestamp'].replace('Z','+00:00')).timestamp() if m.get('timestamp') else 0,
                                    m['role']!='user',str(m.get('id',''))))
        return messages[-limit:] if limit else messages

    def get_work_context(self, work_id: str) -> Dict:
        """获取work的上下文信息"""
        try:
            history = self.history_manager.get_work_history(work_id)
            return history.get("context", {})
        except Exception as e:
            logger.error(f"获取上下文失败: {e}")
            return {}


    def get_session_by_work_id(self, work_id: str, user_id: int) -> Optional[ChatSession]:
        """通过work_id获取session"""
        try:
            session = self.db_session.query(ChatSession)\
                .filter(ChatSession.work_id == work_id)\
                .filter(ChatSession.created_by == user_id)\
                .first()
            return session
        except Exception as e:
            logger.error(f"获取session失败: {e}")
            return None


    def get_chat_statistics(self, work_id: str) -> Dict:
        """获取聊天统计信息"""
        try:
            messages = self.history_manager.get_messages(work_id)

            # 统计各种类型的消息
            total_messages = len(messages)
            user_messages = len(
                [m for m in messages if m.get('role') == 'user'])
            assistant_messages = len(
                [m for m in messages if m.get('role') == 'assistant'])
            json_card_messages = len(
                [m for m in messages if m.get('message_type') == 'json_card'])

            # 统计JSON块类型
            json_block_types = {}
            for msg in messages:
                json_blocks = msg.get('json_blocks', [])
                for block in json_blocks:
                    block_type = block.get('type', 'unknown')
                    json_block_types[block_type] = json_block_types.get(
                        block_type, 0) + 1

            return {
                "total_messages": total_messages,
                "user_messages": user_messages,
                "assistant_messages": assistant_messages,
                "json_card_messages": json_card_messages,
                "json_block_types": json_block_types,
                "format_version": self.history_manager.get_work_history(work_id).get("version", "1.0")
            }
        except Exception as e:
            logger.error(f"获取聊天统计信息失败: {e}")
            return {}
