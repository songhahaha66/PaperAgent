"""
AI系统环境配置管理
从数据库获取API密钥和模型配置，支持多模型、多用户配置
"""

import os
import logging
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
import litellm

logger = logging.getLogger(__name__)


class DatabaseConfigManager:
    """从数据库获取配置信息"""
    
    def __init__(self, db_session: Session):
        self.db_session = db_session
    
    def get_model_config(self, system_type: str):
        """获取指定系统类型的模型配置"""
        from models.models import ModelConfig
        
        config = self.db_session.query(ModelConfig).filter(
            ModelConfig.type == system_type,
            ModelConfig.is_active == True
        ).first()
        
        if not config:
            raise ValueError(f"未找到系统类型 {system_type} 的配置")
        return config
    
    def get_api_key(self, system_type: str) -> str:
        """获取指定系统类型的API密钥"""
        config = self.get_model_config(system_type)
        return config.api_key
    
    def get_model_info(self, system_type: str) -> Dict[str, Any]:
        """获取模型信息（不包含敏感信息）"""
        config = self.get_model_config(system_type)
        return {
            "model_id": config.model_id,
            "base_url": config.base_url,
            "is_active": config.is_active
        }


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
            # 确保工作空间目录存在
            os.makedirs(self.workspace_dir, exist_ok=True)
            logger.info(f"工作空间目录设置完成: {self.workspace_dir}")
            
            # 设置为环境变量，供其他组件使用
            os.environ["WORKSPACE_DIR"] = self.workspace_dir
    
    def initialize_system(self, system_type: str):
        """初始化指定系统类型"""
        try:
            config = self.config_manager.get_model_config(system_type)
            if not config or not config.is_active:
                raise ValueError(f"系统 {system_type} 未配置或未激活")
            
            # 设置LiteLLM配置
            litellm.api_key = config.api_key
            if config.base_url:
                litellm.api_base = config.base_url
            
            logger.info(f"系统 {system_type} 初始化成功，模型: {config.model_id}")
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


def setup_environment_from_db(db_session: Session, workspace_path: Optional[str] = None) -> AIEnvironmentManager:
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

