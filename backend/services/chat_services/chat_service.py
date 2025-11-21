"""
简化版聊天记录管理服务
使用JSON文件存储聊天记录，数据库只存储会话元数据
支持JSON卡片格式的聊天记录
"""

import logging
import time
from typing import List, Optional, Dict
from sqlalchemy.orm import Session

from models.models import ChatSession, User
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

    def add_message(self, work_id: str, role: str, content: str, metadata: Optional[Dict] = None):
        """添加消息到JSON文件（兼容旧格式）"""
        try:
            self.history_manager.save_message(work_id, role, content, metadata)
            logger.info(f"消息已保存到JSON: {work_id}, 角色: {role}")
        except Exception as e:
            logger.error(f"保存消息失败: {e}")
            raise

    def add_json_card_message(self, work_id: str, role: str, content: str,
                              json_blocks: List[Dict] = None, metadata: Optional[Dict] = None):
        """添加JSON卡片格式的消息"""
        try:
            self.history_manager.save_json_card_message(
                work_id, role, content, json_blocks, metadata)
            logger.info(
                f"JSON卡片消息已保存: {work_id}, 角色: {role}, 块数: {len(json_blocks or [])}")
        except Exception as e:
            logger.error(f"保存JSON卡片消息失败: {e}")
            raise

    def add_json_block_to_message(self, work_id: str, message_id: str, json_block: Dict):
        """向指定消息添加JSON块"""
        try:
            success = self.history_manager.add_json_block_to_message(
                work_id, message_id, json_block)
            if success:
                logger.info(
                    f"JSON块已添加到消息: {work_id}, 消息ID: {message_id}, 类型: {json_block.get('type')}")
            return success
        except Exception as e:
            logger.error(f"添加JSON块失败: {e}")
            return False

    def get_work_chat_history(self, work_id: str, limit: Optional[int] = None) -> List[Dict]:
        """获取work的聊天记录（原始格式）"""
        try:
            messages = self.history_manager.get_messages(work_id, limit)
            logger.info(f"获取聊天记录: {work_id}, 数量: {len(messages)}")
            return messages
        except Exception as e:
            logger.error(f"获取聊天记录失败: {e}")
            return []

    def get_work_chat_history_for_frontend(self, work_id: str, limit: Optional[int] = None) -> List[Dict]:
        """获取work的聊天记录（前端格式）"""
        try:
            messages = self.history_manager.get_messages_for_frontend(
                work_id, limit)
            logger.info(f"获取前端格式聊天记录: {work_id}, 数量: {len(messages)}")
            return messages
        except Exception as e:
            logger.error(f"获取前端格式聊天记录失败: {e}")
            return []

    def get_work_context(self, work_id: str) -> Dict:
        """获取work的上下文信息"""
        try:
            history = self.history_manager.get_work_history(work_id)
            return history.get("context", {})
        except Exception as e:
            logger.error(f"获取上下文失败: {e}")
            return {}

    def update_work_context(self, work_id: str, context_updates: Dict):
        """更新work的上下文"""
        try:
            self.history_manager.update_context(work_id, context_updates)
            logger.info(f"上下文已更新: {work_id}")
        except Exception as e:
            logger.error(f"更新上下文失败: {e}")
            raise

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

    def delete_work_session(self, work_id: str, user_id: int) -> bool:
        """删除work对应的session和聊天记录"""
        try:
            # 删除数据库中的session记录
            session = self.get_session_by_work_id(work_id, user_id)
            if session:
                self.db_session.delete(session)
                self.db_session.commit()

            # 清空JSON聊天记录
            self.history_manager.clear_history(work_id)

            logger.info(f"删除work会话: {work_id}")
            return True

        except Exception as e:
            logger.error(f"删除work会话失败: {e}")
            self.db_session.rollback()
            return False

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
