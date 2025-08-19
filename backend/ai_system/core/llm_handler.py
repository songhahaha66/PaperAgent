"""
LLM通信处理器
处理与litellm API的所有通信，包括流式响应和工具调用
"""

import logging
import json
from typing import List, Dict, Any
import litellm

from .stream_manager import StreamOutputManager

logger = logging.getLogger(__name__)


class LLMHandler:
    """
    处理与 litellm API 的所有通信，包括流式响应和工具调用。
    """

    def __init__(self, model: str , stream_manager: StreamOutputManager = None):
        self.model = model
        self.stream_manager = stream_manager
        logger.info(f"LLMHandler初始化完成，模型: {model}")

    async def process_stream(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None):
        """
        调用 LLM API 并处理流式响应，返回完整的响应和工具调用信息。
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
            response_stream = litellm.completion(
                model=self.model,
                messages=messages,
                tools=tools,
                stream=True,
            )

            full_response_content = ""
            tool_calls = []
            current_tool_call = {"id": None, "name": None, "arguments": ""}
            chunk_count = 0

            # 使用异步迭代器处理流式响应
            import asyncio
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
                    await asyncio.sleep(0)

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
