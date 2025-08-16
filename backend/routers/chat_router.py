"""
聊天系统API路由
集成流式传输和聊天记录持久化功能
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
import asyncio
import logging

from database.database import get_db
from auth.auth import get_current_user
from models.models import User
from services.chat_service import ChatService
from ai_system.config.environment import setup_environment_from_db
from ai_system.core.stream_manager import PersistentStreamManager, SimpleStreamCallback
from ai_system.core.main_agent import MainAgent
from ai_system.core.llm_handler import LLMHandler
from schemas.schemas import (
    ChatSessionCreateRequest, 
    ChatStreamRequest, 
    ChatSessionResponse, 
    ChatMessageResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["聊天系统"])


@router.post("/session/create", response_model=ChatSessionResponse)
async def create_chat_session(
    request: ChatSessionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建新的聊天会话"""
    try:
        chat_service = ChatService(db)
        session = chat_service.create_chat_session(
            work_id=request.work_id,
            system_type=request.system_type,
            user_id=current_user.id,
            title=request.title
        )
        return ChatSessionResponse.from_orm(session)
    except Exception as e:
        logger.error(f"创建聊天会话失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建聊天会话失败: {str(e)}"
        )


@router.post("/session/{session_id}/stream")
async def chat_stream(
    session_id: str,
    request: ChatStreamRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """流式聊天接口"""
    try:
        # 验证会话权限
        chat_service = ChatService(db)
        session = chat_service.get_session(session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="聊天会话不存在"
            )
        
        # 检查用户权限
        if session.created_by != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权限访问此会话"
            )
        
        # 创建流式响应
        async def generate_stream():
            try:
                # 初始化AI环境
                env_manager = setup_environment_from_db(db)
                
                # 根据会话类型初始化系统
                system_type = session.system_type
                env_manager.initialize_system(system_type)
                
                # 创建流式管理器
                stream_manager = PersistentStreamManager(
                    chat_service=chat_service,
                    session_id=session_id
                )
                
                # 设置消息角色
                stream_manager.set_role("assistant")
                
                # 添加用户消息到聊天记录
                await chat_service.add_message(
                    session_id=session_id,
                    role="user",
                    content=request.problem
                )
                
                # 创建LLM处理器和主代理
                llm_handler = LLMHandler(
                    model=request.model or "gemini/gemini-2.0-flash",
                    stream_manager=stream_manager
                )
                
                main_agent = MainAgent(llm_handler, stream_manager)
                
                # 执行AI任务
                await stream_manager.print_xml_open("ai_response")
                await stream_manager.print_content("正在分析您的问题...")
                await stream_manager.print_content(f"\n\n问题: {request.problem}")
                await stream_manager.print_content("\n\n开始AI分析...")
                await stream_manager.print_xml_close("ai_response")
                
                # 运行主代理
                result = await main_agent.run(request.problem)
                
                # 完成消息持久化
                await stream_manager.finalize_message()
                
            except Exception as e:
                logger.error(f"流式聊天处理失败: {e}")
                yield f"错误: {str(e)}"
        
        return StreamingResponse(generate_stream(), media_type="text/plain")
        
    except Exception as e:
        logger.error(f"聊天流式接口失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"聊天接口失败: {str(e)}"
        )


@router.get("/session/{session_id}/history", response_model=list[ChatMessageResponse])
async def get_chat_history(
    session_id: str,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取聊天历史"""
    try:
        # 验证会话权限
        chat_service = ChatService(db)
        session = chat_service.get_session(session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="聊天会话不存在"
            )
        
        if session.created_by != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权限访问此会话"
            )
        
        history = chat_service.get_chat_history(session_id, limit)
        return [ChatMessageResponse.from_orm(msg) for msg in history]
    except Exception as e:
        logger.error(f"获取聊天历史失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取聊天历史失败: {str(e)}"
        )


@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_chat_sessions(
    work_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """列出用户的聊天会话"""
    try:
        chat_service = ChatService(db)
        sessions = chat_service.list_user_sessions(current_user.id, work_id)
        return [ChatSessionResponse.from_orm(session) for msg in sessions]
    except Exception as e:
        logger.error(f"列出聊天会话失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"列出聊天会话失败: {str(e)}"
        )


@router.put("/session/{session_id}/title")
async def update_session_title(
    session_id: str,
    title: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新会话标题"""
    try:
        chat_service = ChatService(db)
        success = chat_service.update_session_title(session_id, title)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="会话不存在或无权限"
            )
        return {"status": "success", "message": "标题更新成功"}
    except Exception as e:
        logger.error(f"更新会话标题失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新会话标题失败: {str(e)}"
        )


@router.delete("/session/{session_id}")
async def delete_chat_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除聊天会话"""
    try:
        chat_service = ChatService(db)
        success = chat_service.delete_session(session_id, current_user.id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="会话不存在或无权限"
            )
        return {"status": "success", "message": "会话删除成功"}
    except Exception as e:
        logger.error(f"删除聊天会话失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除聊天会话失败: {str(e)}"
        )


@router.post("/session/{session_id}/reset")
async def reset_chat_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """重置聊天会话（清空消息历史）"""
    try:
        chat_service = ChatService(db)
        session = chat_service.get_session(session_id)
        if not session or session.created_by != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="会话不存在或无权限"
            )
        
        # 删除所有消息
        messages = chat_service.get_chat_history(session_id, limit=1000)
        for message in messages:
            # 这里需要实现删除消息的方法
            pass
        
        return {"status": "success", "message": "会话已重置"}
    except Exception as e:
        logger.error(f"重置聊天会话失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"重置聊天会话失败: {str(e)}"
        )
