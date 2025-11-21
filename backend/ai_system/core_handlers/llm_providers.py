"""
AI提供商抽象层
支持多种AI提供商的统一接口
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from langchain_core.language_models import BaseLanguageModel
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    """AI提供商抽象基类"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.provider_name = config.get('provider', 'unknown')
        self.model_id = config.get('model_id', '')
        self.api_key = config.get('api_key', '')
        self.base_url = config.get('base_url', '')
        self.is_active = config.get('is_active', True)

    @abstractmethod
    def create_llm_instance(self, **kwargs) -> BaseLanguageModel:
        """创建LLM实例"""
        pass

    @abstractmethod
    def validate_config(self) -> bool:
        """验证配置是否有效"""
        pass

    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息"""
        return {
            "provider": self.provider_name,
            "model_id": self.model_id,
            "base_url": self.base_url,
            "is_active": self.is_active
        }


class OpenAIProvider(BaseLLMProvider):
    """OpenAI提供商 - 支持OpenAI及兼容格式"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.provider_name = "openai"

    def create_llm_instance(self, **kwargs) -> ChatOpenAI:
        """创建ChatOpenAI实例"""
        if not self.validate_config():
            raise ValueError(f"OpenAI提供商配置无效: {self.get_model_info()}")

        return ChatOpenAI(
            model=self.model_id,
            api_key=self.api_key,
            base_url=self.base_url if self.base_url else None,
            temperature=kwargs.get('temperature', 0.7),
            max_tokens=kwargs.get('max_tokens', 4000),
            streaming=kwargs.get('streaming', True)
        )

    def validate_config(self) -> bool:
        """验证OpenAI配置"""
        return bool(self.api_key and self.model_id)


class AnthropicProvider(BaseLLMProvider):
    """Anthropic提供商 - 支持Claude系列"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.provider_name = "anthropic"

    def create_llm_instance(self, **kwargs) -> ChatAnthropic:
        """创建ChatAnthropic实例"""
        if not self.validate_config():
            raise ValueError(f"Anthropic提供商配置无效: {self.get_model_info()}")

        return ChatAnthropic(
            model=self.model_id,
            api_key=self.api_key,
            base_url=self.base_url if self.base_url else None,
            temperature=kwargs.get('temperature', 0.7),
            max_tokens=kwargs.get('max_tokens', 4000)
        )

    def validate_config(self) -> bool:
        """验证Anthropic配置"""
        return bool(self.api_key and self.model_id)


class GoogleProvider(BaseLLMProvider):
    """Google提供商 - 支持Gemini系列"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.provider_name = "google"

    def create_llm_instance(self, **kwargs) -> ChatGoogleGenerativeAI:
        """创建ChatGoogleGenerativeAI实例"""
        if not self.validate_config():
            raise ValueError(f"Google提供商配置无效: {self.get_model_info()}")

        return ChatGoogleGenerativeAI(
            model=self.model_id,
            google_api_key=self.api_key,
            temperature=kwargs.get('temperature', 0.7),
            max_tokens=kwargs.get('max_tokens', 4000)
        )

    def validate_config(self) -> bool:
        """验证Google配置"""
        return bool(self.api_key and self.model_id)


class LocalProvider(BaseLLMProvider):
    """本地模型提供商 - 支持Ollama等本地服务"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.provider_name = "local"

    def create_llm_instance(self, **kwargs) -> ChatOpenAI:
        """通过OpenAI兼容格式连接本地模型"""
        if not self.validate_config():
            raise ValueError(f"本地模型提供商配置无效: {self.get_model_info()}")

        # 本地模型通常使用OpenAI兼容格式
        return ChatOpenAI(
            model=self.model_id,
            api_key=self.api_key or "fake-key",  # 本地模型可能不需要真实API key
            base_url=self.base_url or "http://localhost:11434/v1",  # 默认Ollama地址
            temperature=kwargs.get('temperature', 0.7),
            max_tokens=kwargs.get('max_tokens', 4000),
            streaming=kwargs.get('streaming', True)
        )

    def validate_config(self) -> bool:
        """验证本地模型配置"""
        # 本地模型主要需要base_url，model_id通常也是必需的
        return bool(self.base_url or self.model_id)


class LLMProviderFactory:
    """LLM提供商工厂类"""

    _providers = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "google": GoogleProvider,
        "local": LocalProvider
    }

    @classmethod
    def create_provider(cls, config: Dict[str, Any]) -> BaseLLMProvider:
        """根据配置创建提供商实例"""
        provider_name = config.get('provider', 'openai').lower()

        if provider_name not in cls._providers:
            raise ValueError(f"不支持的AI提供商: {provider_name}，支持的提供商: {list(cls._providers.keys())}")

        provider_class = cls._providers[provider_name]
        return provider_class(config)

    @classmethod
    def create_llm_instance(cls, config: Dict[str, Any], **kwargs) -> BaseLanguageModel:
        """直接创建LLM实例"""
        provider = cls.create_provider(config)
        return provider.create_llm_instance(**kwargs)

    @classmethod
    def get_supported_providers(cls) -> List[str]:
        """获取支持的提供商列表"""
        return list(cls._providers.keys())

    @classmethod
    def register_provider(cls, name: str, provider_class: type):
        """注册新的提供商"""
        if not issubclass(provider_class, BaseLLMProvider):
            raise ValueError("提供商类必须继承自BaseLLMProvider")
        cls._providers[name.lower()] = provider_class


def create_llm_from_model_config(model_config, **kwargs) -> BaseLanguageModel:
    """
    从数据库ModelConfig对象创建LLM实例的便捷函数

    Args:
        model_config: 数据库ModelConfig对象
        **kwargs: 额外的LLM参数

    Returns:
        LLM实例
    """
    config = {
        'provider': getattr(model_config, 'provider', 'openai'),
        'model_id': model_config.model_id,
        'api_key': model_config.api_key,
        'base_url': model_config.base_url,
        'is_active': getattr(model_config, 'is_active', True)
    }

    return LLMProviderFactory.create_llm_instance(config, **kwargs)


def create_smolagents_model_from_config(model_config, **kwargs):
    """
    从数据库ModelConfig对象创建SmolAgents原生模型实例

    Args:
        model_config: 数据库ModelConfig对象
        **kwargs: 额外的模型参数

    Returns:
        SmolAgents原生模型实例
    """
    try:
        from smolagents.models import OpenAIServerModel

        provider = getattr(model_config, 'provider', 'openai').lower()
        model_id = model_config.model_id
        api_key = model_config.api_key
        base_url = model_config.base_url

        logger.info(f"创建SmolAgents模型: {provider}/{model_id}")

        # 根据provider创建对应的SmolAgents模型
        if provider == 'openai' or provider == 'local':
            # OpenAI兼容模型，包括本地服务
            return OpenAIServerModel(
                model_id=model_id,
                api_key=api_key,
                api_base=base_url,
                **kwargs
            )
        elif provider == 'anthropic':
            # Anthropic通过OpenAI兼容接口使用
            return OpenAIServerModel(
                model_id=model_id,
                api_key=api_key,
                api_base="https://api.anthropic.com",
                **kwargs
            )
        elif provider == 'azure':
            # Azure OpenAI
            return OpenAIServerModel(
                model_id=model_id,
                api_key=api_key,
                api_base=base_url,
                **kwargs
            )
        else:
            # 默认使用OpenAI兼容格式
            logger.warning(f"提供商 {provider} 使用OpenAI兼容格式")
            return OpenAIServerModel(
                model_id=model_id,
                api_key=api_key,
                api_base=base_url,
                **kwargs
            )

    except ImportError:
        logger.error("SmolAgents未安装，无法创建模型实例")
        raise ImportError("请安装smolagents库: pip install smolagents")
    except Exception as e:
        logger.error(f"创建SmolAgents模型失败: {e}")
        raise ValueError(f"创建SmolAgents模型失败: {str(e)}")