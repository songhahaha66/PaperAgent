"""
聊天系统API路由
提供AI对话、工具调用等功能
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime
from auth.auth import get_current_user
from models.models import User
from ai_system.chat_system import chat_system
import uuid

router = APIRouter(prefix="/chat", tags=["聊天系统"])


class ChatMessageRequest(BaseModel):
    """聊天消息请求"""
    message: str
    work_id: str
    system_type: str = "brain"  # brain, code, writing


class ChatMessageResponse(BaseModel):
    """聊天消息响应"""
    success: bool
    response: Optional[str] = None
    tool_calls: Optional[List[dict]] = None
    tool_results: Optional[List[dict]] = None
    session_id: str
    timestamp: str
    error: Optional[str] = None


class ChatHistoryResponse(BaseModel):
    """聊天历史响应"""
    session_id: str
    messages: List[dict]
    total_count: int


@router.post("/send", response_model=ChatMessageResponse)
async def send_message(
    request: ChatMessageRequest,
    current_user: User = Depends(get_current_user)
):
    """
    发送聊天消息
    
    - **message**: 用户消息内容
    - **work_id**: 工作ID
    - **system_type**: 系统类型 (brain, code, writing)
    """
    try:
        # 生成会话ID（使用用户ID和工作ID的组合）
        session_id = f"{current_user.id}_{request.work_id}_{request.system_type}"
        
        # 处理消息
        result = await chat_system.process_message(
            session_id=session_id,
            user_message=request.message,
            work_id=request.work_id,
            system_type=request.system_type
        )
        
        return ChatMessageResponse(**result)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"处理消息失败: {str(e)}")


@router.get("/history/{session_id}", response_model=ChatHistoryResponse)
async def get_chat_history(
    session_id: str,
    max_messages: int = Query(50, ge=1, le=100, description="最大消息数量"),
    current_user: User = Depends(get_current_user)
):
    """
    获取聊天历史
    
    - **session_id**: 会话ID
    - **max_messages**: 最大消息数量（1-100）
    """
    try:
        # 验证会话是否属于当前用户
        if not session_id.startswith(f"{current_user.id}_"):
            raise HTTPException(status_code=403, detail="无权访问此会话")
        
        messages = chat_system.get_session_history(session_id, max_messages)
        
        return ChatHistoryResponse(
            session_id=session_id,
            messages=messages,
            total_count=len(messages)
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取聊天历史失败: {str(e)}")


@router.delete("/session/{session_id}")
async def delete_chat_session(
    session_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    删除聊天会话
    
    - **session_id**: 会话ID
    """
    try:
        # 验证会话是否属于当前用户
        if not session_id.startswith(f"{current_user.id}_"):
            raise HTTPException(status_code=403, detail="无权删除此会话")
        
        chat_system.delete_session(session_id)
        
        return {"message": "会话删除成功"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除会话失败: {str(e)}")


@router.get("/tools")
async def get_available_tools(current_user: User = Depends(get_current_user)):
    """
    获取可用的工具列表
    """
    try:
        from ai_system.tool_framework import tool_registry
        tools = tool_registry.get_tools_dict()
        
        return {
            "tools": tools,
            "total_count": len(tools)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取工具列表失败: {str(e)}")


@router.post("/refresh-configs")
async def refresh_model_configs(current_user: User = Depends(get_current_user)):
    """
    刷新AI模型配置
    """
    try:
        from ai_system.litellm_client import litellm_client
        litellm_client.refresh_configs()
        
        return {"message": "模型配置刷新成功"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新配置失败: {str(e)}")
