"""
LLM通信处理器
处理与litellm API的所有通信，包括流式响应和工具调用
"""

import logging
import json
from typing import List, Dict, Any
import litellm
import asyncio
from concurrent.futures import ThreadPoolExecutor
import functools

from .stream_manager import StreamOutputManager

logger = logging.getLogger(__name__)


class LLMHandler:
    """
    处理与 litellm API 的所有通信，包括流式响应和工具调用。
    完全异步化，避免阻塞事件循环。
    """

    def __init__(self, model: str, stream_manager: StreamOutputManager = None):
        self.model = model
        self.stream_manager = stream_manager
        # 创建线程池执行器，用于处理同步的litellm调用
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="LLMHandler")
        logger.info(f"LLMHandler初始化完成，模型: {model}")

    async def process_stream(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None):
        """
        异步调用 LLM API 并处理流式响应，返回完整的响应和工具调用信息。
        使用线程池避免阻塞事件循环。
        """
        logger.info(f"开始调用LLM API，消息数量: {len(messages)}")
        
        # 打印消息列表的详细信息，帮助调试
        logger.info("=== 消息列表详情 ===")
        for i, msg in enumerate(messages):
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')[:1000]  # 只显示前100个字符
            logger.info(f"消息 {i}: role={role}, content={repr(content)}...")
        logger.info("=== 消息列表结束 ===")
        
        if tools:
            logger.info(f"使用工具数量: {len(tools)}")

        try:
            # 在线程池中执行同步的litellm调用，避免阻塞事件循环
            loop = asyncio.get_event_loop()
            response_stream = await loop.run_in_executor(
                self.executor,
                functools.partial(
                    litellm.completion,
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    stream=True
                )
            )

            full_response_content = ""
            tool_calls = []
            current_tool_call = {"id": None, "name": None, "arguments": ""}
            chunk_count = 0

            # 异步处理流式响应
            for chunk in response_stream:
                chunk_count += 1
                delta = chunk.choices[0].delta

                # 1. 累积文本内容
                if delta.content:
                    content = delta.content
                    if self.stream_manager:
                        await self.stream_manager.print_stream(content)
                    else:
                        print(content, end="", flush=True)
                    full_response_content += content
                    logger.debug(
                        f"接收到文本内容块 {chunk_count}: {repr(content[:30])}...")
                    
                    # 让出控制权，确保WebSocket能及时发送数据
                    await asyncio.sleep(0.001)  # 使用更小的延迟

                # 2. 累积工具调用信息
                if delta.tool_calls:
                    for tool_call_delta in delta.tool_calls:
                        if tool_call_delta.id:
                            # 如果是新的 tool_call，保存上一个
                            if current_tool_call["id"] is not None:
                                tool_calls.append(
                                    self._finalize_tool_call(current_tool_call))
                            current_tool_call = {
                                "id": tool_call_delta.id, "name": None, "arguments": ""}
                            logger.debug(f"开始新的工具调用: {tool_call_delta.id}")

                        if tool_call_delta.function:
                            if tool_call_delta.function.name:
                                current_tool_call["name"] = tool_call_delta.function.name
                                logger.debug(
                                    f"工具调用名称: {tool_call_delta.function.name}")
                            if tool_call_delta.function.arguments:
                                current_tool_call["arguments"] += tool_call_delta.function.arguments
                                logger.debug(
                                    f"工具调用参数累积: {len(current_tool_call['arguments'])} 字符")

                # 定期让出控制权，确保其他异步任务能够执行
                if chunk_count % 10 == 0:
                    await asyncio.sleep(0.001)

            # 添加最后一个工具调用
            if current_tool_call["id"] is not None:
                tool_calls.append(self._finalize_tool_call(current_tool_call))

            if not self.stream_manager:
                print()  # 确保换行

            logger.info(f"LLM API调用完成，总块数: {chunk_count}，工具调用数: {len(tool_calls)}")

            # 确保触发消息完成回调
            if self.stream_manager:
                await self.stream_manager.finalize_message()

            # 构建完整的 assistant 消息
            assistant_message = {"role": "assistant",
                                 "content": full_response_content}
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls

            return assistant_message, tool_calls

        except Exception as e:
            logger.error(f"LLM API调用失败: {e}")
            error_message = f"LLM API调用失败: {str(e)}"
            if self.stream_manager:
                await self.stream_manager.print_content(error_message)
            
            # 返回错误消息
            return {"role": "assistant", "content": error_message}, []

    async def process_sync(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None):
        """
        同步调用LLM API（在线程池中执行），用于不需要流式处理的场景
        """
        logger.info(f"开始同步调用LLM API，消息数量: {len(messages)}")
        
        try:
            # 在线程池中执行同步调用
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                self.executor,
                functools.partial(
                    litellm.completion,
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    stream=False
                )
            )
            
            content = response.choices[0].message.content
            tool_calls = []
            
            # 处理工具调用
            if hasattr(response.choices[0].message, 'tool_calls') and response.choices[0].message.tool_calls:
                for tool_call in response.choices[0].message.tool_calls:
                    tool_calls.append({
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments
                        }
                    })
            
            logger.info(f"同步LLM API调用完成，内容长度: {len(content)}，工具调用数: {len(tool_calls)}")
            
            return {"role": "assistant", "content": content}, tool_calls
            
        except Exception as e:
            logger.error(f"同步LLM API调用失败: {e}")
            error_message = f"LLM API调用失败: {str(e)}"
            return {"role": "assistant", "content": error_message}, []

    def _finalize_tool_call(self, tool_call_data: Dict) -> Dict:
        """构建完整的工具调用对象"""
        return {
            "id": tool_call_data["id"],
            "type": "function",
            "function": {
                "name": tool_call_data["name"],
                "arguments": tool_call_data["arguments"]
            }
        }

    def set_model(self, model: str):
        """设置LLM模型"""
        self.model = model
        logger.info(f"LLM模型已更新为: {model}")

    def get_model_info(self) -> Dict[str, Any]:
        """获取当前模型信息"""
        return {
            "model": self.model,
            "stream_manager_configured": self.stream_manager is not None
        }

    async def close(self):
        """关闭线程池执行器"""
        if self.executor:
            self.executor.shutdown(wait=True)
            logger.info("LLMHandler线程池已关闭")

    def __del__(self):
        """析构函数，确保线程池被正确关闭"""
        if hasattr(self, 'executor') and self.executor:
            self.executor.shutdown(wait=False)
