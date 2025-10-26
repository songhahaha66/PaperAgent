"""
LLM通信处理器
重构支持多种AI提供商的LLM通信处理器，基于 LangChain Agent
"""

import logging
import json
from typing import List, Dict, Any, Optional, AsyncGenerator
from langchain_core.language_models import BaseLanguageModel
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# 使用字符串类型注解避免循环导入（类型仍用字符串）
# 运行期需要的配置模块安全地在模块级导入
from ..config.async_config import AsyncConfig
from .llm_providers import LLMProviderFactory, create_llm_from_model_config

logger = logging.getLogger(__name__)


class LLMHandler:
    """
    支持多AI提供商的LLM处理器，基于 LangChain Agent，支持按步骤流式传输代理进度
    """

    def __init__(self,
                 model: Optional[str] = None,
                 stream_manager: Optional['StreamOutputManager'] = None,
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

        # 构建配置字典
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

        # 创建论文写作专用的 prompt
        self.system_prompt = "你是一个专业的论文写作助手，能够帮助用户生成、修改和完善学术论文。请仔细分析用户需求，并调用相应的工具来完成任务。"
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        logger.info(f"多提供商LLMHandler初始化完成，提供商: {self.provider}, 模型: {self.model}")

    def _convert_messages_to_input(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """将消息列表转换为 Agent 输入格式"""
        # 新版 LangChain agent 期望 messages 格式
        return {
            "messages": messages
        }

    def _convert_to_langchain_messages(self, messages: List[Dict[str, Any]]) -> List:
        """将消息列表转换为 LangChain 消息格式"""
        langchain_messages = []

        for message in messages:
            role = message.get("role", "")
            content = message.get("content", "")

            if role == "system":
                langchain_messages.append(SystemMessage(content=content))
            elif role == "user":
                langchain_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                langchain_messages.append(AIMessage(content=content))
            else:
                # 默认作为用户消息处理
                langchain_messages.append(HumanMessage(content=content))

        return langchain_messages

    async def _handle_agent_step(self, step: str, data: Dict[str, Any]) -> str:
        """处理代理步骤并返回可显示的内容"""
        if step == "agent":
            # LLM 节点，处理 AI 响应
            if "messages" in data and data["messages"]:
                message = data["messages"][-1]
                if hasattr(message, 'content') and message.content:
                    return message.content
                elif hasattr(message, 'content_blocks'):
                    # 处理内容块
                    content = ""
                    for block in message.content_blocks:
                        if hasattr(block, 'text'):
                            content += block.text
                    return content

        elif step == "tools":
            # 工具节点，处理工具执行结果
            if "messages" in data and data["messages"]:
                message = data["messages"][-1]
                if hasattr(message, 'content'):
                    return f"\n🔧 工具执行结果: {message.content}\n"

        return ""

    async def process_stream(self, messages: List[Dict[str, Any]], tools: Optional[List[Any]] = None):
        """
        基于 LangChain Agent 的流式处理，按步骤传输代理进度
        """
        logger.info(f"开始调用 LangChain Agent，消息数量: {len(messages)}")

        if tools:
            logger.info(f"使用工具数量: {len(tools)}")

        try:
            # 如果有工具，创建 agent
            if tools:
                # 转换工具为 LangChain 格式
                agent = create_agent(self.llm, tools=tools, system_prompt=self.system_prompt)

                # 转换消息格式
                input_data = self._convert_messages_to_input(messages)

                # 简化处理：直接调用 agent
                try:
                    result = await agent.ainvoke(input_data)

                    # 新版 LangChain agent 返回 messages 列表
                    messages = result.get("messages", [])
                    content = ""

                    # 找到最后一条 AI 消息
                    for message in reversed(messages):
                        if isinstance(message, dict) and message.get("role") == "assistant":
                            content = message.get("content", "")
                            break
                        elif hasattr(message, 'content'):
                            content = message.content or ""
                            break

                    # 如果没有找到 AI 消息，使用 messages 的最后一个元素
                    if not content and messages:
                        last_message = messages[-1]
                        if isinstance(last_message, dict):
                            content = last_message.get("content", "")
                        elif hasattr(last_message, 'content'):
                            content = last_message.content or ""

                    # 发送到流式管理器
                    if self.stream_manager:
                        await self.stream_manager.print_stream(content)
                        await self.stream_manager.finalize_message()
                    else:
                        print(content)

                    assistant_message = {"role": "assistant", "content": content}
                    return assistant_message, []

                except Exception as e:
                    logger.error(f"Agent 调用失败: {e}")
                    # 降级为直接 LLM 调用
                    langchain_messages = self._convert_to_langchain_messages(messages)
                    response = await self.llm.ainvoke(langchain_messages)

                    content = response.content or ""

                    if self.stream_manager:
                        await self.stream_manager.print_stream(content)
                        await self.stream_manager.finalize_message()
                    else:
                        print(content)

                    return {"role": "assistant", "content": content}, []

            else:
                # 没有工具的情况，直接调用 LLM
                langchain_messages = self._convert_to_langchain_messages(messages)
                response = await self.llm.ainvoke(langchain_messages)

                content = response.content or ""

                if self.stream_manager:
                    await self.stream_manager.print_stream(content)
                    await self.stream_manager.finalize_message()
                else:
                    print(content)

                logger.info(f"LangChain LLM 调用完成，内容长度: {len(content)}")

                return {"role": "assistant", "content": content}, []

        except Exception as e:
            logger.error(f"LangChain 调用失败: {e}", exc_info=True)
            error_message = f"LLM 调用失败: {str(e)}"
            if self.stream_manager:
                await self.stream_manager.print_content(error_message)

            return {"role": "assistant", "content": error_message}, []

    async def process_sync(self, messages: List[Dict[str, Any]], tools: Optional[List[Any]] = None):
        """
        同步调用 LangChain LLM（非流式），用于不需要流式处理的场景
        """
        logger.info(f"开始同步调用 LangChain LLM，消息数量: {len(messages)}")

        try:
            # 如果有工具，创建 agent
            if tools:
                agent = create_agent(self.llm, tools=tools, system_prompt=self.system_prompt)

                # 转换消息格式
                input_data = self._convert_messages_to_input(messages)

                # 同步调用
                result = await agent.ainvoke(input_data)

                # 新版 LangChain agent 返回 messages 列表
                messages = result.get("messages", [])
                content = ""

                # 找到最后一条 AI 消息
                for message in reversed(messages):
                    if isinstance(message, dict) and message.get("role") == "assistant":
                        content = message.get("content", "")
                        break
                    elif hasattr(message, 'content'):
                        content = message.content or ""
                        break

                # 如果没有找到 AI 消息，使用 messages 的最后一个元素
                if not content and messages:
                    last_message = messages[-1]
                    if isinstance(last_message, dict):
                        content = last_message.get("content", "")
                    elif hasattr(last_message, 'content'):
                        content = last_message.content or ""

                logger.info(f"同步 LangChain Agent 调用完成，内容长度: {len(content)}")

                return {"role": "assistant", "content": content}, []

            else:
                # 没有工具的情况，直接调用 LLM
                langchain_messages = self._convert_to_langchain_messages(messages)
                response = await self.llm.ainvoke(langchain_messages)

                content = response.content or ""

                logger.info(f"同步 LangChain LLM 调用完成，内容长度: {len(content)}")

                return {"role": "assistant", "content": content}, []

        except Exception as e:
            logger.error(f"同步 LangChain LLM 调用失败: {e}", exc_info=True)
            error_message = f"LLM 调用失败: {str(e)}"
            return {"role": "assistant", "content": error_message}, []

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
            "supported_providers": LLMProviderFactory.get_supported_providers()
        }

    async def close(self):
        """清理资源"""
        logger.info("LangChain LLMHandler 资源清理完成")
