"""
LiteLLM客户端配置模块
支持多种AI模型的统一接口
"""
import os
from typing import Dict, Any, Optional, List
from litellm import completion, acompletion
from models.models import ModelConfig
from database.database import get_db
from sqlalchemy.orm import Session


class LiteLLMClient:
    """LiteLLM客户端，支持多种AI模型"""
    
    def __init__(self):
        self.model_configs: Dict[str, ModelConfig] = {}
        self._load_model_configs()
    
    def _load_model_configs(self):
        """从数据库加载模型配置"""
        try:
            db = next(get_db())
            configs = db.query(ModelConfig).filter(ModelConfig.is_active == True).all()
            for config in configs:
                self.model_configs[config.type] = config
        except Exception as e:
            print(f"加载模型配置失败: {e}")
    
    def get_model_config(self, model_type: str) -> Optional[ModelConfig]:
        """获取指定类型的模型配置"""
        return self.model_configs.get(model_type)
    
    def refresh_configs(self):
        """刷新模型配置"""
        self._load_model_configs()
    
    async def chat_completion(
        self,
        model_type: str,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        异步聊天完成
        
        Args:
            model_type: 模型类型 (brain, code, writing)
            messages: 对话消息列表
            tools: 可用工具列表
            tool_choice: 工具选择策略
            **kwargs: 其他参数
        
        Returns:
            AI响应结果
        """
        config = self.get_model_config(model_type)
        if not config:
            raise ValueError(f"未找到类型为 {model_type} 的模型配置")
        
        # 设置环境变量
        os.environ["OPENAI_API_KEY"] = config.api_key
        os.environ["OPENAI_API_BASE"] = config.base_url
        
        try:
            # 构建请求参数
            request_params = {
                "model": config.model_id,
                "messages": messages,
                **kwargs
            }
            
            # 如果提供了工具，添加到请求中
            if tools:
                request_params["tools"] = tools
                if tool_choice:
                    request_params["tool_choice"] = tool_choice
            
            # 调用LiteLLM
            response = await acompletion(**request_params)
            
            return {
                "content": response.choices[0].message.content,
                "tool_calls": getattr(response.choices[0].message, 'tool_calls', None),
                "model": config.model_id,
                "usage": response.usage.dict() if response.usage else None
            }
            
        except Exception as e:
            raise Exception(f"AI模型调用失败: {e}")
    
    def chat_completion_sync(
        self,
        model_type: str,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        同步聊天完成
        
        Args:
            model_type: 模型类型 (brain, code, writing)
            messages: 对话消息列表
            tools: 可用工具列表
            tool_choice: 工具选择策略
            **kwargs: 其他参数
        
        Returns:
            AI响应结果
        """
        config = self.get_model_config(model_type)
        if not config:
            raise ValueError(f"未找到类型为 {model_type} 的模型配置")
        
        # 设置环境变量
        os.environ["OPENAI_API_KEY"] = config.api_key
        os.environ["OPENAI_API_BASE"] = config.base_url
        
        try:
            # 构建请求参数
            request_params = {
                "model": config.model_id,
                "messages": messages,
                **kwargs
            }
            
            # 如果提供了工具，添加到请求中
            if tools:
                request_params["tools"] = tools
                if tool_choice:
                    request_params["tool_choice"] = tool_choice
            
            # 调用LiteLLM
            response = completion(**request_params)
            
            return {
                "content": response.choices[0].message.content,
                "tool_calls": getattr(response.choices[0].message, 'tool_calls', None),
                "model": config.model_id,
                "usage": response.usage.dict() if response.usage else None
            }
            
        except Exception as e:
            raise Exception(f"AI模型调用失败: {e}")


# 全局LiteLLM客户端实例
litellm_client = LiteLLMClient()
