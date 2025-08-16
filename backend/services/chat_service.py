"""
聊天记录管理服务
提供聊天会话创建、消息存储、历史记录查询等功能
"""

import logging
import time
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc

from models.models import ChatSession, ChatMessage, User
from schemas.schemas import ChatSessionCreateRequest, ChatMessageCreateRequest

logger = logging.getLogger(__name__)


class ChatService:
    """聊天服务"""
    
    def __init__(self, db_session: Session):
        self.db_session = db_session
    
    def create_chat_session(self, work_id: str, system_type: str, user_id: int, title: str = None) -> ChatSession:
        """创建新的聊天会话"""
        try:
            session_id = f"{work_id}_{system_type}_{int(time.time())}"
            
            # 创建ChatSession记录
            session = ChatSession(
                session_id=session_id,
                work_id=work_id,
                system_type=system_type,
                title=title or f"{system_type}对话",
                created_by=user_id
            )
            
            self.db_session.add(session)
            self.db_session.commit()
            self.db_session.refresh(session)
            
            logger.info(f"创建聊天会话成功: {session_id}")
            return session
            
        except Exception as e:
            logger.error(f"创建聊天会话失败: {e}")
            self.db_session.rollback()
            raise
    
    def add_message(self, session_id: str, role: str, content: str, 
                   tool_calls: Dict = None, tool_results: Dict = None, message_metadata: Dict = None) -> ChatMessage:
        """添加聊天消息"""
        try:
            # 创建ChatMessage记录
            message = ChatMessage(
                session_id=session_id,
                role=role,
                content=content,
                tool_calls=tool_calls,
                tool_results=tool_results,
                message_metadata=message_metadata
            )
            
            self.db_session.add(message)
            self.db_session.commit()
            self.db_session.refresh(message)
            
            logger.info(f"添加聊天消息成功: {session_id}, 角色: {role}, 长度: {len(content)}")
            return message
            
        except Exception as e:
            logger.error(f"添加聊天消息失败: {e}")
            self.db_session.rollback()
            raise
    
    def get_chat_history(self, session_id: str, limit: int = 50) -> List[ChatMessage]:
        """获取聊天历史"""
        try:
            messages = self.db_session.query(ChatMessage)\
                .filter(ChatMessage.session_id == session_id)\
                .order_by(ChatMessage.created_at.desc())\
                .limit(limit)\
                .all()
            
            # 按时间正序返回
            messages.reverse()
            logger.info(f"获取聊天历史: {session_id}, 限制: {limit}, 实际数量: {len(messages)}")
            return messages
            
        except Exception as e:
            logger.error(f"获取聊天历史失败: {e}")
            return []
    
    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """获取会话信息"""
        try:
            session = self.db_session.query(ChatSession)\
                .filter(ChatSession.session_id == session_id)\
                .first()
            
            logger.info(f"获取会话信息: {session_id}, 结果: {'成功' if session else '不存在'}")
            return session
            
        except Exception as e:
            logger.error(f"获取会话信息失败: {e}")
            return None
    
    def list_user_sessions(self, user_id: int, work_id: str = None) -> List[ChatSession]:
        """列出用户的聊天会话"""
        try:
            query = self.db_session.query(ChatSession)\
                .filter(ChatSession.created_by == user_id)
            
            if work_id:
                query = query.filter(ChatSession.work_id == work_id)
            
            sessions = query.order_by(ChatSession.updated_at.desc()).all()
            
            logger.info(f"列出用户会话: {user_id}, 工作ID: {work_id}, 数量: {len(sessions)}")
            return sessions
            
        except Exception as e:
            logger.error(f"列出用户会话失败: {e}")
            return []
    
    def update_session_title(self, session_id: str, title: str) -> bool:
        """更新会话标题"""
        try:
            session = self.get_session(session_id)
            if not session:
                return False
            
            session.title = title
            self.db_session.commit()
            
            logger.info(f"更新会话标题: {session_id}, 新标题: {title}")
            return True
            
        except Exception as e:
            logger.error(f"更新会话标题失败: {e}")
            self.db_session.rollback()
            return False
    
    def delete_session(self, session_id: str, user_id: int) -> bool:
        """删除聊天会话"""
        try:
            session = self.get_session(session_id)
            if not session or session.created_by != user_id:
                return False
            
            # 删除会话（消息会通过cascade自动删除）
            self.db_session.delete(session)
            self.db_session.commit()
            
            logger.info(f"删除聊天会话: {session_id}, 用户: {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"删除聊天会话失败: {e}")
            self.db_session.rollback()
            return False
    
    def get_session_message_count(self, session_id: str) -> int:
        """获取会话的消息数量"""
        try:
            count = self.db_session.query(ChatMessage)\
                .filter(ChatMessage.session_id == session_id)\
                .count()
            return count
        except Exception as e:
            logger.error(f"获取会话消息数量失败: {e}")
            return 0
    
    def archive_session(self, session_id: str, user_id: int) -> bool:
        """归档聊天会话"""
        try:
            session = self.get_session(session_id)
            if not session or session.created_by != user_id:
                return False
            
            session.status = "archived"
            self.db_session.commit()
            
            logger.info(f"归档聊天会话: {session_id}, 用户: {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"归档聊天会话失败: {e}")
            self.db_session.rollback()
            return False
