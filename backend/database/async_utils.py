"""
异步数据库上下文管理器和工具函数
提供安全的异步数据库会话管理
"""

import logging
from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager

from database.database import get_async_db_session
from services.async_chat_service import AsyncChatService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def async_db_context() -> AsyncGenerator[AsyncSession, None]:
    """异步数据库会话上下文管理器"""
    session = None
    try:
        session = await get_async_db_session()
        yield session
    except Exception as e:
        if session:
            try:
                # 某些情况下session可能没有rollback方法或已经关闭
                pass  # 暂时跳过rollback，确保session关闭
            except:
                pass
        logger.error(f"数据库操作失败: {e}")
        raise
    finally:
        if session:
            await session.close()


@asynccontextmanager
async def async_chat_service_context() -> AsyncGenerator[AsyncChatService, None]:
    """异步聊天服务上下文管理器"""
    session = None
    chat_service = None
    try:
        session = await get_async_db_session()
        chat_service = AsyncChatService(session)
        yield chat_service
    except Exception as e:
        if session:
            try:
                # 某些情况下session可能没有rollback方法或已经关闭
                pass  # 暂时跳过rollback，确保session关闭
            except:
                pass
        logger.error(f"聊天服务操作失败: {e}")
        raise
    finally:
        if chat_service:
            await chat_service.close()
        if session:
            await session.close()


def safe_compare_ids(obj_attr, user_id) -> bool:
    """安全比较对象属性和用户ID"""
    try:
        # 将两个值都转换为字符串进行比较
        obj_val = str(obj_attr) if obj_attr is not None else ""
        user_val = str(user_id) if user_id is not None else ""
        return obj_val == user_val
    except Exception as e:
        logger.error(f"ID比较失败: {e}")
        return False


class AsyncDatabaseManager:
    """异步数据库管理器，用于WebSocket等长连接场景"""
    
    def __init__(self):
        self.session: Optional[AsyncSession] = None
        self.chat_service: Optional[AsyncChatService] = None
    
    async def initialize(self):
        """初始化数据库连接和服务"""
        try:
            self.session = await get_async_db_session()
            self.chat_service = AsyncChatService(self.session)
            logger.info("异步数据库管理器初始化成功")
        except Exception as e:
            logger.error(f"异步数据库管理器初始化失败: {e}")
            await self.close()
            raise
    
    async def get_chat_service(self) -> AsyncChatService:
        """获取聊天服务实例"""
        if not self.chat_service:
            await self.initialize()
        assert self.chat_service is not None
        return self.chat_service
    
    async def get_session(self) -> AsyncSession:
        """获取数据库会话"""
        if not self.session:
            await self.initialize()
        assert self.session is not None
        return self.session
    
    async def close(self):
        """关闭数据库连接"""
        try:
            if self.chat_service:
                await self.chat_service.close()
                self.chat_service = None
            
            if self.session:
                await self.session.close()
                self.session = None
                
            logger.info("异步数据库管理器已关闭")
        except Exception as e:
            logger.error(f"关闭异步数据库管理器失败: {e}")
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()
        if exc_type:
            logger.error(f"数据库管理器上下文异常: {exc_type.__name__}: {exc_val}")
        return False
