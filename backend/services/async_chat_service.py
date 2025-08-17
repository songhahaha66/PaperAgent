"""
异步聊天记录管理服务
提供聊天会话创建、消息存储、历史记录查询等功能的异步版本
"""

import logging
import time
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc, select

from models.models import ChatSession, ChatMessage, User
from schemas.schemas import ChatSessionCreateRequest, ChatMessageCreateRequest

logger = logging.getLogger(__name__)


class AsyncChatService:
    """异步聊天服务"""
    
    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session
    
    async def create_chat_session(self, work_id: str, system_type: str, user_id: int, title: Optional[str] = None) -> ChatSession:
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
            await self.db_session.commit()
            await self.db_session.refresh(session)
            
            logger.info(f"创建聊天会话成功: {session_id}")
            return session
            
        except Exception as e:
            logger.error(f"创建聊天会话失败: {e}")
            await self.db_session.rollback()
            raise
    
    async def add_message(self, session_id: str, role: str, content: str, 
                   tool_calls: Optional[Dict] = None, tool_results: Optional[Dict] = None, message_metadata: Optional[Dict] = None) -> ChatMessage:
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
            await self.db_session.commit()
            await self.db_session.refresh(message)
            
            logger.info(f"添加聊天消息成功: {session_id}, 角色: {role}, 长度: {len(content)}")
            return message
            
        except Exception as e:
            logger.error(f"添加聊天消息失败: {e}")
            await self.db_session.rollback()
            raise
    
    async def get_chat_history(self, session_id: str, limit: int = 50) -> List[ChatMessage]:
        """获取聊天历史"""
        try:
            stmt = select(ChatMessage)\
                .where(ChatMessage.session_id == session_id)\
                .order_by(ChatMessage.created_at.desc())\
                .limit(limit)
            
            result = await self.db_session.execute(stmt)
            messages = result.scalars().all()
            
            # 按时间正序返回
            messages = list(reversed(messages))
            logger.info(f"获取聊天历史: {session_id}, 限制: {limit}, 实际数量: {len(messages)}")
            return messages
            
        except Exception as e:
            logger.error(f"获取聊天历史失败: {e}")
            return []
    
    async def get_chat_history_objects(self, session_id: str, limit: int = 50) -> List[ChatMessage]:
        """获取聊天历史对象，用于上下文维护"""
        try:
            stmt = select(ChatMessage)\
                .where(ChatMessage.session_id == session_id)\
                .where(ChatMessage.role.in_(["user", "assistant"]))\
                .order_by(ChatMessage.created_at.asc())\
                .limit(limit)
            
            result = await self.db_session.execute(stmt)
            messages = result.scalars().all()
            
            logger.info(f"获取聊天历史对象: {session_id}, 限制: {limit}, 实际数量: {len(messages)}")
            return list(messages)
            
        except Exception as e:
            logger.error(f"获取聊天历史对象失败: {e}")
            return []
    
    async def get_session(self, session_id: str) -> Optional[ChatSession]:
        """获取会话信息"""
        try:
            stmt = select(ChatSession).where(ChatSession.session_id == session_id)
            result = await self.db_session.execute(stmt)
            session = result.scalar_one_or_none()
            
            logger.info(f"获取会话信息: {session_id}, 存在: {session is not None}")
            return session
            
        except Exception as e:
            logger.error(f"获取会话信息失败: {e}")
            return None
    
    async def get_user_sessions(self, user_id: int, limit: int = 50, offset: int = 0) -> List[ChatSession]:
        """获取用户的会话列表"""
        try:
            stmt = select(ChatSession)\
                .where(ChatSession.created_by == user_id)\
                .order_by(ChatSession.created_at.desc())\
                .limit(limit)\
                .offset(offset)
            
            result = await self.db_session.execute(stmt)
            sessions = result.scalars().all()
            
            logger.info(f"获取用户会话列表: 用户ID {user_id}, 数量: {len(sessions)}")
            return list(sessions)
            
        except Exception as e:
            logger.error(f"获取用户会话列表失败: {e}")
            return []
    
    async def update_session_title(self, session_id: str, title: str, user_id: int) -> bool:
        """更新会话标题"""
        try:
            stmt = select(ChatSession)\
                .where(ChatSession.session_id == session_id)\
                .where(ChatSession.created_by == user_id)
            
            result = await self.db_session.execute(stmt)
            session = result.scalar_one_or_none()
            
            if not session:
                logger.warning(f"未找到会话或无权限: {session_id}")
                return False
            
            session.title = title  # type: ignore
            await self.db_session.commit()
            
            logger.info(f"更新会话标题成功: {session_id} -> {title}")
            return True
            
        except Exception as e:
            logger.error(f"更新会话标题失败: {e}")
            await self.db_session.rollback()
            return False
    
    async def delete_session(self, session_id: str, user_id: int) -> bool:
        """删除会话及其所有消息"""
        try:
            # 首先验证会话存在且用户有权限
            session_stmt = select(ChatSession)\
                .where(ChatSession.session_id == session_id)\
                .where(ChatSession.created_by == user_id)
            
            session_result = await self.db_session.execute(session_stmt)
            session = session_result.scalar_one_or_none()
            
            if not session:
                logger.warning(f"未找到会话或无权限: {session_id}")
                return False
            
            # 删除所有相关消息
            message_stmt = select(ChatMessage).where(ChatMessage.session_id == session_id)
            message_result = await self.db_session.execute(message_stmt)
            messages = message_result.scalars().all()
            
            for message in messages:
                await self.db_session.delete(message)
            
            # 删除会话
            await self.db_session.delete(session)
            await self.db_session.commit()
            
            logger.info(f"删除会话成功: {session_id}, 删除消息数: {len(messages)}")
            return True
            
        except Exception as e:
            logger.error(f"删除会话失败: {e}")
            await self.db_session.rollback()
            return False
    
    async def reset_session(self, session_id: str, user_id: int) -> bool:
        """重置会话（删除所有消息但保留会话）"""
        try:
            # 首先验证会话存在且用户有权限
            session_stmt = select(ChatSession)\
                .where(ChatSession.session_id == session_id)\
                .where(ChatSession.created_by == user_id)
            
            session_result = await self.db_session.execute(session_stmt)
            session = session_result.scalar_one_or_none()
            
            if not session:
                logger.warning(f"未找到会话或无权限: {session_id}")
                return False
            
            # 删除所有相关消息
            message_stmt = select(ChatMessage).where(ChatMessage.session_id == session_id)
            message_result = await self.db_session.execute(message_stmt)
            messages = message_result.scalars().all()
            
            for message in messages:
                await self.db_session.delete(message)
            
            await self.db_session.commit()
            
            logger.info(f"重置会话成功: {session_id}, 删除消息数: {len(messages)}")
            return True
            
        except Exception as e:
            logger.error(f"重置会话失败: {e}")
            await self.db_session.rollback()
            return False
    
    async def close(self):
        """关闭数据库会话"""
        if self.db_session:
            await self.db_session.close()
