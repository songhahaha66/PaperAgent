"""
文件管理相关路由模块
"""

from .file import router as file_router
from .template import router as template_router

__all__ = ['file_router', 'template_router']
