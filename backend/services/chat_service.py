"""
简化版聊天记录管理服务
使用JSON文件存储聊天记录，数据库只存储会话元数据
"""

import logging
import time
from typing import List, Optional, Dict
from sqlalchemy.orm import Session

from models.models import ChatSession, User
from services.chat_history_manager import ChatHistoryManager

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
        """添加消息到JSON文件"""
        try:
            self.history_manager.save_message(work_id, role, content, metadata)
            logger.info(f"消息已保存到JSON: {work_id}, 角色: {role}")
        except Exception as e:
            logger.error(f"保存消息失败: {e}")
            raise
    
    def get_work_chat_history(self, work_id: str, limit: Optional[int] = None) -> List[Dict]:
        """获取work的聊天记录"""
        try:
            messages = self.history_manager.get_messages(work_id, limit)
            logger.info(f"获取聊天记录: {work_id}, 数量: {len(messages)}")
            return messages
        except Exception as e:
            logger.error(f"获取聊天记录失败: {e}")
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