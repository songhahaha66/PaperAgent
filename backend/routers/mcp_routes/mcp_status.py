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
    获取MCP服务器状态
    
    返回MCP客户端的可用性状态、工具数量等信息
    如果初始化失败，返回错误信息
    
    Returns:
        Dict包含:
        - available: MCP是否可用
        - status: 状态描述
        - error: 错误信息（如果有）
    """
    try:
        # 从app.state获取MCP管理器
        mcp_manager = getattr(request.app.state, 'mcp_manager', None)
        mcp_available = getattr(request.app.state, 'mcp_available', False)
        
        if not mcp_manager:
            return {
                "available": False,
                "status": "not_initialized",
                "message": "MCP客户端未初始化",
                "error": "MCP manager not found in application state"
            }
        
        if not mcp_available:
            return {
                "available": False,
                "status": "initialization_failed",
                "message": "MCP客户端初始化失败",
                "error": "MCP client initialization failed during startup"
            }
        
        # 检查MCP客户端是否可用
        is_available = mcp_manager.is_available()
        
        if is_available:
            return {
                "available": True,
                "status": "connected",
                "message": "MCP客户端已连接并可用",
                "client_info": str(mcp_manager)
            }
        else:
            return {
                "available": False,
                "status": "not_available",
                "message": "MCP客户端不可用",
                "error": "MCP client is not available"
            }
            
    except Exception as e:
        logger.error(f"获取MCP状态时出错: {e}", exc_info=True)
        return {
            "available": False,
            "status": "error",
            "message": "获取MCP状态时发生错误",
            "error": str(e)
        }
