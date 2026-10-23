"""
路由模块包
统一管理所有API路由
"""

from .auth_routes import auth_router
from .chat_routes import chat_router
from .work_routes import work_router, workspace_router
from .file_routes import template_router
from .config_routes import model_config_router
from .mcp_routes import mcp_router
from .experience_routes import router as experience_router
from .resource_routes import router as resource_router

# 所有路由列表，方便在主应用中注册
all_routers = [
    resource_router,
    experience_router,
    auth_router,
    chat_router,
    work_router,
    workspace_router,
    template_router,
    model_config_router,
    mcp_router
]

__all__ = [
    'auth_router',
    'chat_router', 
    'work_router',
    'workspace_router',
    'template_router',
    'model_config_router',
    'mcp_router',
    'all_routers'
]


# Apply the same version/ownership gate to all HTTP project routes, including future routes.
from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection
from database.database import get_db
from auth.auth import verify_token
from services.file_services.project_access import require_project


def project_route_access(request: HTTPConnection, db=Depends(get_db)):
    if request.scope['type'] == 'websocket':
        return
    work_id = request.path_params.get('work_id')
    if not work_id:
        return
    authorization = request.headers.get('authorization', '')
    token = authorization[7:] if authorization.lower().startswith('bearer ') else ''
    user_id = verify_token(token) if token else None
    if user_id is None:
        raise HTTPException(401, 'Authentication required')
    metadata = request.scope.get('method') == 'GET' and request.url.path == '/api/works/' + work_id
    exporting = request.scope.get('method') == 'GET' and request.url.path == '/api/workspace/' + work_id + '/export'
    require_project(db, work_id, user_id, exporting=metadata or exporting)
