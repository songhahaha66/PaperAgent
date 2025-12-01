"""
MCP状态管理API路由
提供MCP服务器状态查询功能
"""

from fastapi import APIRouter, Request
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["MCP管理"])


@router.get("/status")
async def get_mcp_status(request: Request) -> Dict[str, Any]:
    """
    获取Word工具状态（MCP已移除，现使用直接集成）
    
    返回Word工具的可用性状态
    
    Returns:
        Dict包含:
        - available: Word工具是否可用
        - status: 状态描述
        - message: 状态信息
    """
    try:
        # MCP已移除，Word工具现在直接集成
        return {
            "available": True,
            "status": "direct_integration",
            "message": "Word工具已直接集成，无需MCP",
            "integration_type": "direct"
        }
            
    except Exception as e:
        logger.error(f"获取Word工具状态时出错: {e}", exc_info=True)
        return {
            "available": False,
            "status": "error",
            "message": "获取Word工具状态时发生错误",
            "error": str(e)
        }
