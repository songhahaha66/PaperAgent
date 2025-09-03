"""
路由模块包
统一管理所有API路由
"""

from .auth_routes import auth_router
from .chat_routes import chat_router
from .work_routes import work_router, workspace_router
from .file_routes import file_router, template_router
from .config_routes import model_config_router, context_router

# 所有路由列表，方便在主应用中注册
all_routers = [
    auth_router,
    chat_router,
    work_router,
    workspace_router,
    file_router,
    template_router,
    model_config_router,
    context_router
]

__all__ = [
    'auth_router',
    'chat_router', 
    'work_router',
    'workspace_router',
    'file_router',
    'template_router',
    'model_config_router',
    'context_router',
    'all_routers'
]
