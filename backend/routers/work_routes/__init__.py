"""
工作管理相关路由模块
"""

from .work import router as work_router
from .workspace import router as workspace_router

__all__ = ['work_router', 'workspace_router']
