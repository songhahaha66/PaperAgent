"""
LLM处理器 - LangChain 重构版本
简化的LLM处理器，专注于创建LLM实例和Agent，不再处理复杂的流式逻辑
"""

import logging
from typing import Optional, Any, Dict
from langchain_core.language_models import BaseLanguageModel

from .llm_providers import LLMProviderFactory, create_llm_from_model_config
from ..core_agents.main_agent import MainAgent

logger = logging.getLogger(__name__)


class LLMHandler:
    """
    简化的LLM处理器
    - 只负责创建LangChain LLM实例
    - 提供创建Agent的便捷方法
    - 不再处理复杂的流式输出逻辑
    """

    def __init__(self,
                 model: Optional[str] = None,
                 stream_manager=None,
                 provider: Optional[str] = None,
                 api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 model_config: Optional[Any] = None,
                 **llm_kwargs):
        """
        初始化LLM处理器

        Args:
            model: 模型ID（可选，如果提供了model_config则从中获取）
            stream_manager: 流式输出管理器
            provider: AI提供商名称（可选，如果提供了model_config则从中获取）
            api_key: API密钥（可选，如果提供了model_config则从中获取）
            base_url: API基础URL（可选，如果提供了model_config则从中获取）
            model_config: 数据库ModelConfig对象
            **llm_kwargs: 传递给LLM实例的额外参数
        """
        self.stream_manager = stream_manager
        self.llm_kwargs = {
            'temperature': llm_kwargs.get('temperature', 0.7),
            'max_tokens': llm_kwargs.get('max_tokens', 4000),
            'streaming': llm_kwargs.get('streaming', True)
        }

        # 构建配置字典并创建LLM实例
        if model_config:
            # 从数据库配置对象创建LLM实例
            self.llm = create_llm_from_model_config(model_config, **self.llm_kwargs)
            self.provider = getattr(model_config, 'provider', 'openai')
            self.model = model_config.model_id
            logger.info(f"LLMHandler初始化完成，提供商: {self.provider}, 模型: {self.model}")
        else:
            # 从直接参数创建LLM实例
            config = {
                'provider': provider or 'openai',
                'model_id': model,
                'api_key': api_key,
                'base_url': base_url,
                'is_active': True
            }
            self.llm = LLMProviderFactory.create_llm_instance(config, **self.llm_kwargs)
            self.provider = provider or 'openai'
            self.model = model
            logger.info(f"LLMHandler初始化完成，提供商: {self.provider}, 模型: {self.model}")

        logger.info(f"简化LLMHandler初始化完成，提供商: {self.provider}, 模型: {self.model}")

    def create_main_agent(self, workspace_dir: str = None, work_id: str = None,
                         template_id: int = None) -> MainAgent:
        """
        创建 MainAgent 实例的便捷方法

        Args:
            workspace_dir: 工作空间目录
            work_id: 工作ID
            template_id: 模板ID

        Returns:
            MainAgent 实例
        """
        return MainAgent(
            llm=self.llm,
            stream_manager=self.stream_manager,
            workspace_dir=workspace_dir,
            work_id=work_id,
            template_id=template_id
        )

    # CodeAgent 由 code_agent.py 提供 LangChain 实现，可按需直接创建

    def get_llm_instance(self) -> BaseLanguageModel:
        """
        获取原始的 LangChain LLM 实例
        用于高级用例，直接使用 LangChain 功能

        Returns:
            LangChain LLM 实例
        """
        return self.llm

    def set_model(self, model: str, provider: Optional[str] = None, **kwargs):
        """
        设置LLM模型和提供商

        Args:
            model: 模型ID
            provider: AI提供商名称（可选，默认使用当前提供商）
            **kwargs: 其他LLM参数
        """
        self.model = model
        if provider:
            self.provider = provider

        # 构建配置并创建新的LLM实例
        config = {
            'provider': self.provider or 'openai',
            'model_id': model,
            'api_key': kwargs.get('api_key', ''),
            'base_url': kwargs.get('base_url', ''),
            'is_active': True
        }

        # 更新LLM参数
        llm_params = {**self.llm_kwargs, **kwargs}
        self.llm = LLMProviderFactory.create_llm_instance(config, **llm_params)
        logger.info(f"LLM已更新，提供商: {self.provider}, 模型: {model}")

    def get_model_info(self) -> Dict[str, Any]:
        """获取当前模型信息"""
        return {
            "provider": self.provider,
            "model": self.model,
            "stream_manager_configured": self.stream_manager is not None,
            "langchain_based": True,
            "simplified": True,
            "supported_providers": LLMProviderFactory.get_supported_providers()
        }

    async def close(self):
        """清理资源"""
        logger.info("简化 LLMHandler 资源清理完成")
