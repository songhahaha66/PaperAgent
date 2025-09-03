"""
配置管理相关路由模块
"""

from .model_config import router as model_config_router
from .context import router as context_router

__all__ = ['model_config_router', 'context_router']
