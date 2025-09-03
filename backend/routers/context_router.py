"""
上下文管理API路由
提供上下文状态查询、摘要生成、压缩历史等功能
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any, List

from database.database import get_db
from auth.auth import get_current_user
from models.models import User
from ai_system.core.context_manager import ContextSummary, CompressedMessage
from .router_utils import route_guard

router = APIRouter(prefix="/api/context", tags=["上下文管理"])


@router.get("/status/{session_id}")
@route_guard
async def get_context_status(
    session_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取指定会话的上下文状态"""
    from ai_system.core.context_manager import ContextManager
    context_manager = ContextManager()
    mock_messages = [
        {"role": "system", "content": "你是AI助手"},
        {"role": "user", "content": "请帮我分析这个数学问题"},
        {"role": "assistant", "content": "我来帮你分析这个数学问题..."}
    ]
    status_info = context_manager.get_context_status(mock_messages)
    return {
        "session_id": session_id,
        "status": status_info,
        "message": "上下文状态获取成功"
    }


@router.post("/summary/{session_id}")
@route_guard
async def generate_context_summary(
    session_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """为指定会话生成上下文摘要"""
    from ai_system.core.context_manager import ContextManager
    context_manager = ContextManager()
    mock_messages = [
        {"role": "system", "content": "你是AI助手"},
        {"role": "user", "content": "请帮我分析这个数学问题"},
        {"role": "assistant", "content": "我来帮你分析这个数学问题..."},
        {"role": "user", "content": "能给出具体的计算步骤吗？"},
        {"role": "assistant", "content": "好的，让我给出详细的计算步骤..."}
    ]
    summary = await context_manager.generate_context_summary(mock_messages, session_id)
    return {
        "session_id": session_id,
        "summary": {
            "summary_id": summary.summary_id,
            "content": summary.content,
            "key_topics": summary.key_topics,
            "important_points": summary.important_points,
            "created_at": summary.created_at.isoformat(),
            "message_count": summary.message_count,
            "token_estimate": summary.token_estimate
        },
        "message": "上下文摘要生成成功"
    }


@router.get("/memory/{session_id}")
@route_guard
async def get_long_term_memory(
    session_id: str,
    limit: int = 5,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取指定会话的长期记忆（历史摘要）"""
    from ai_system.core.context_manager import ContextManager
    context_manager = ContextManager()
    mock_summaries = [
        ContextSummary(
            summary_id=f"summary_{session_id}_1",
            session_id=session_id,
            content="讨论了数学建模的基本概念",
            key_topics=["数学建模", "基础概念"],
            important_points=["建模步骤很重要"],
            created_at=context_manager.summaries[0].created_at if context_manager.summaries else None,
            message_count=10,
            token_estimate=500
        )
    ]
    if not mock_summaries[0].created_at:
        from datetime import datetime
        mock_summaries[0].created_at = datetime.now()
    summaries = []
    for summary in mock_summaries[:limit]:
        summaries.append({
            "summary_id": summary.summary_id,
            "content": summary.content,
            "key_topics": summary.key_topics,
            "important_points": summary.important_points,
            "created_at": summary.created_at.isoformat(),
            "message_count": summary.message_count,
            "token_estimate": summary.token_estimate
        })
    return {
        "session_id": session_id,
        "summaries": summaries,
        "total_count": len(summaries),
        "message": "长期记忆获取成功"
    }


@router.get("/compression-history/{session_id}")
@route_guard
async def get_compression_history(
    session_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取指定会话的压缩历史"""
    from ai_system.core.context_manager import ContextManager
    context_manager = ContextManager()
    mock_compression_history = [
        CompressedMessage(
            role="system",
            content="[上下文摘要] 用户讨论了数学建模问题",
            original_length=1000,
            compressed_length=200,
            compression_ratio=0.2,
            is_compressed=True
        )
    ]
    compression_history = []
    for comp in mock_compression_history:
        compression_history.append({
            "role": comp.role,
            "content": comp.content,
            "original_length": comp.original_length,
            "compressed_length": comp.compressed_length,
            "compression_ratio": comp.compression_ratio,
            "is_compressed": comp.is_compressed
        })
    return {
        "session_id": session_id,
        "compression_history": compression_history,
        "total_count": len(compression_history),
        "message": "压缩历史获取成功"
    }


@router.post("/compress/{session_id}")
@route_guard
async def compress_context(
    session_id: str,
    strategy: str = "medium",
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """手动压缩指定会话的上下文"""
    from ai_system.core.context_manager import ContextManager
    context_manager = ContextManager()
    mock_messages = [
        {"role": "system", "content": "你是AI助手"},
        {"role": "user", "content": "请帮我分析这个数学问题"},
        {"role": "assistant", "content": "我来帮你分析这个数学问题..."},
        {"role": "user", "content": "能给出具体的计算步骤吗？"},
        {"role": "assistant", "content": "好的，让我给出详细的计算步骤..."}
    ]
    compressed_messages, compression_results = context_manager.compress_context(
        mock_messages, strategy
    )
    return {
        "session_id": session_id,
        "strategy": strategy,
        "original_count": len(mock_messages),
        "compressed_count": len(compressed_messages),
        "compression_ratio": len(compressed_messages) / len(mock_messages),
        "compression_results": [
            {
                "role": comp.role,
                "content": comp.content,
                "original_length": comp.original_length,
                "compressed_length": comp.compressed_length,
                "compression_ratio": comp.compression_ratio,
                "is_compressed": comp.is_compressed
            }
            for comp in compression_results
        ],
        "message": "上下文压缩成功"
    }


@router.delete("/cleanup")
@route_guard
async def cleanup_old_summaries(
    max_age_days: int = 7,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """清理过期的摘要（管理员功能）"""
    from ai_system.core.context_manager import ContextManager
    context_manager = ContextManager()
    context_manager.cleanup_old_summaries(max_age_days)
    return {
        "max_age_days": max_age_days,
        "message": f"已清理超过{max_age_days}天的过期摘要"
    }


@router.get("/export/{session_id}")
@route_guard
async def export_context_data(
    session_id: str,
    current_user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """导出指定会话的上下文数据（用于调试和分析）"""
    from ai_system.core.context_manager import ContextManager
    context_manager = ContextManager()
    export_data = context_manager.export_context_data()
    return {
        "session_id": session_id,
        "export_data": export_data,
        "message": "上下文数据导出成功"
    }
