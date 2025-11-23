"""
统一的路径配置管理
确保 pa_data 路径在各种环境下都能正确工作
"""

import os
from pathlib import Path


def get_project_root() -> Path:
    """获取项目根目录（PaperAgent目录）"""
    # 当前文件位置：backend/config/paths.py
    current_file = Path(__file__).resolve()
    backend_dir = current_file.parent.parent  # backend 目录
    candidate_root = backend_dir.parent

    # 如果父目录中包含 backend 文件夹，说明当前处于项目根目录结构
    if (candidate_root / "backend").exists():
        return candidate_root

    # 否则（例如在 Docker 容器中），项目根目录就是 backend 目录本身
    return backend_dir


def get_pa_data_base() -> Path:
    """
    获取 pa_data 基础目录
    
    优先级：
    1. 环境变量 PA_DATA_PATH
    2. 项目根目录下的 pa_data
    """
    # 优先使用环境变量
    env_path = os.getenv("PA_DATA_PATH")
    if env_path:
        return Path(env_path).resolve()
    
    # 默认使用项目根目录下的 pa_data
    return get_project_root() / "pa_data"


def get_workspaces_path() -> Path:
    """获取工作空间目录路径"""
    path = get_pa_data_base() / "workspaces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_templates_path() -> Path:
    """获取模板目录路径"""
    path = get_pa_data_base() / "templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_workspace_path(work_id: str) -> Path:
    """获取指定工作的工作空间路径"""
    path = get_workspaces_path() / work_id
    return path


# 导出常用路径
PA_DATA_BASE = get_pa_data_base()
WORKSPACES_PATH = get_workspaces_path()
TEMPLATES_PATH = get_templates_path()
