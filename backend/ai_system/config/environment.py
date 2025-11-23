"""
AI系统环境配置管理
从数据库获取API密钥和模型配置，支持多模型、多用户配置
"""

import os
import logging
from typing import Optional, Dict, Any, List
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
            logger.error(f"用户 {user_id} 未配置 {system_type}" +
                        (f"，提供商: {provider}" if provider else ""))
            raise ValueError(f"用户 {user_id} 未配置 {system_type}" +
                           (f"，提供商: {provider}" if provider else ""))
        
        logger.info(f"成功加载 {system_type} 配置，提供商: {config.provider}, 模型: {config.model_id}")
        return config

    def get_api_key(self, system_type: str, user_id: int, provider: Optional[str] = None) -> str:
        """获取指定系统类型的API密钥"""
        config = self.get_model_config(system_type, user_id, provider)
        return config.api_key

    def get_model_info(self, system_type: str, user_id: int, provider: Optional[str] = None) -> Dict[str, Any]:
        """获取模型信息（不包含敏感信息）"""
        config = self.get_model_config(system_type, user_id, provider)
        return {
            "provider": getattr(config, 'provider', 'openai'),
            "model_id": config.model_id,
            "base_url": config.base_url,
            "is_active": config.is_active
        }

    def get_available_providers(self, system_type: str, user_id: int) -> List[str]:
        """获取指定系统类型可用的AI提供商列表"""
        from models.models import ModelConfig

        query = self.db_session.query(ModelConfig.provider).filter(
            ModelConfig.type == system_type,
            ModelConfig.is_active == True,
            ModelConfig.created_by == user_id
        )

        providers = [row.provider for row in query.distinct()]
        return providers

    def validate_provider_config(self, system_type: str, provider: str, user_id: int) -> bool:
        """验证指定提供商的配置是否有效"""
        try:
            config = self.get_model_config(system_type, user_id, provider)
            return bool(config.api_key and config.model_id)
        except ValueError:
            return False


class AIEnvironmentManager:
    """AI环境管理器"""

    def __init__(self, db_session: Session):
        self.db_session = db_session
        self.config_manager = DatabaseConfigManager(db_session)
        self.llm_handlers = {}
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

        # 不需要重复创建目录结构，workspace_files.py中已经处理了
        # 设置为环境变量，供其他组件使用
        os.environ["WORKSPACE_DIR"] = self.workspace_dir

    def initialize_system(self, system_type: str, user_id: int = None):
        """初始化指定系统类型"""
        try:
            config = self.config_manager.get_model_config(system_type, user_id)
            if not config or not config.is_active:
                raise ValueError(f"系统 {system_type} 未配置或未激活")

            # 注意：不再设置LiteLLM全局配置，因为系统使用LangChain LLM实例
            # API密钥将通过LLM providers直接传递给具体的LLM实例
            # 这样可以支持多个不同的模型配置同时使用

            logger.info(f"系统 {system_type} 配置验证成功，模型: {config.model_id}")
            return config

        except Exception as e:
            logger.error(f"系统 {system_type} 初始化失败: {e}")
            raise
    
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
