"""
AI系统环境配置管理
从数据库获取API密钥和模型配置，支持多模型、多用户配置
"""

import os
import logging
from typing import Optional
from sqlalchemy.orm import Session
from config.paths import get_workspaces_path

logger = logging.getLogger(__name__)


class DatabaseConfigManager:
    """从数据库获取配置信息"""

    def __init__(self, db_session: Session):
        self.db_session = db_session

    def get_model_config(self, system_type: str, user_id: int, provider: Optional[str] = None):
        """
        获取指定系统类型的模型配置
        优先级：用户特定配置 -> 系统全局配置

        Args:
            system_type: 系统类型（brain, code, writing）
            user_id: 用户ID（可选）
            provider: AI提供商（可选，用于筛选特定提供商的配置）

        Returns:
            ModelConfig对象
        """
        if system_type not in {'brain', 'code', 'writing'}:
            raise ValueError('不支持的模型角色')
        from models.models import ModelConfig

        def query_configs(user_id_filter):
            """内部查询函数"""
            query = self.db_session.query(ModelConfig).filter(
                ModelConfig.type == system_type,
                ModelConfig.is_active == True
            )

            if user_id_filter is not None:
                query = query.filter(ModelConfig.created_by == user_id_filter)

            if provider is not None:
                query = query.filter(ModelConfig.provider == provider)

            return query.first()

        config = None

        # 强制要求用户ID，确保每个用户只能调用自己的模型
        if user_id is None:
            raise ValueError("必须指定用户ID才能获取模型配置")

        # 只获取用户特定ID的配置，严格权限控制
        config = query_configs(user_id_filter=user_id)

        if not config:
            from .api_settings import EnvModelConfig, load_env_api_settings

            settings = load_env_api_settings()
            if settings:
                logger.info(
                    "用户 %s 未配置 %s，回退到 PAPERAGENT_API_* 环境变量 (%s)",
                    user_id,
                    system_type,
                    settings.model_id,
                )
                return EnvModelConfig(settings, system_type=system_type)

            logger.error(f"用户 {user_id} 未配置 {system_type}" +
                        (f"，提供商: {provider}" if provider else ""))
            raise ValueError(f"用户 {user_id} 未配置 {system_type}" +
                           (f"，提供商: {provider}" if provider else ""))

        logger.info(f"成功加载 {system_type} 配置，提供商: {config.provider}, 模型: {config.model_id}")
        return config


class AIEnvironmentManager:
    """AI环境管理器"""

    def __init__(self, db_session: Session):
        self.db_session = db_session
        self.config_manager = DatabaseConfigManager(db_session)
        self.workspace_dir = None

    def setup_workspace(self, workspace_path: Optional[str] = None):
        """设置工作空间目录"""
        if workspace_path:
            self.workspace_dir = workspace_path
        else:
            # 使用统一的路径配置
            self.workspace_dir = str(get_workspaces_path())

        # 确保工作空间目录存在
        os.makedirs(self.workspace_dir, exist_ok=True)
        logger.info(f"工作空间目录设置完成: {self.workspace_dir}")


    def get_workspace_dir(self) -> str:
        """获取工作空间目录"""
        if not self.workspace_dir:
            raise ValueError("工作空间目录未设置，请先调用 setup_workspace()")
        return self.workspace_dir

    def validate_environment(self) -> bool:
        """验证环境配置是否完整"""
        try:
            # 检查工作空间目录
            if not self.workspace_dir or not os.path.exists(self.workspace_dir):
                logger.error("工作空间目录未设置或不存在")
                return False

            # 检查数据库连接
            if not self.db_session:
                logger.error("数据库会话未建立")
                return False

            logger.info("环境配置验证通过")
            return True

        except Exception as e:
            logger.error(f"环境配置验证失败: {e}")
            return False


def setup_environment_from_db(db_session: Session, workspace_path: Optional[str] = None, user_id: int = None) -> AIEnvironmentManager:
    """
    从数据库设置环境配置
    替代原来的 setup_environment() 函数
    """
    logger.info("开始从数据库初始化AI环境...")

    env_manager = AIEnvironmentManager(db_session)
    env_manager.setup_workspace(workspace_path)

    # 验证环境配置
    if not env_manager.validate_environment():
        raise RuntimeError("AI环境配置验证失败")

    logger.info("AI环境初始化完成")
    return env_manager
