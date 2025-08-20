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
from ..config.async_config import AsyncConfig

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
            # 改为收集所有工具调用chunk，不进行实时解析
            tool_call_chunks = []
            chunk_count = 0

            # 异步处理流式响应
            for chunk in response_stream:
                chunk_count += 1
                delta = chunk.choices[0].delta

                # 1. 累积文本内容（保持不变）
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
                    # 使用配置参数优化延迟
                    config = AsyncConfig.get_llm_stream_config()
                    if chunk_count % config["yield_interval"] == 0:
                        await asyncio.sleep(config["yield_delay"])

                # 2. 收集工具调用chunk，但不解析（改为完全等待模式）
                if delta.tool_calls:
                    # 只收集chunk，不进行任何解析
                    chunk_info = {
                        "chunk_id": chunk_count,
                        "delta": delta.tool_calls
                    }
                    tool_call_chunks.append(chunk_info)
                    
                    # 添加详细的调试日志
                    tool_call_count = len(delta.tool_calls)
                    logger.debug(f"收集工具调用chunk {chunk_count}，包含 {tool_call_count} 个工具调用")
                    
                    # 如果chunk数量异常多，记录警告
                    if chunk_count > 1000:
                        logger.warning(f"工具调用chunk数量异常多: {chunk_count}，可能存在无限循环")
                    
                    # 记录前几个chunk的详细信息
                    if chunk_count <= 5:
                        for i, tool_call in enumerate(delta.tool_calls):
                            logger.debug(f"  Chunk {chunk_count} 工具调用 {i}: id={tool_call.id}, name={getattr(tool_call.function, 'name', 'None') if tool_call.function else 'None'}")

                # 定期让出控制权，确保其他异步任务能够执行
                config = AsyncConfig.get_llm_stream_config()
                if chunk_count % (config["yield_interval"] * 2) == 0:
                    await asyncio.sleep(config["yield_delay"])

            # 流式响应结束后，统一处理所有工具调用（完全等待模式）
            if tool_call_chunks:
                logger.info(f"开始处理 {len(tool_call_chunks)} 个工具调用chunk")
                try:
                    tool_calls = self._extract_tool_calls_from_chunks(tool_call_chunks)
                    logger.info(f"成功提取 {len(tool_calls)} 个完整工具调用")
                except Exception as e:
                    logger.error(f"工具调用提取失败: {e}", exc_info=True)
                    tool_calls = []
            else:
                logger.info("没有工具调用需要处理")

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

    def _extract_tool_calls_from_chunks(self, tool_call_chunks: List[Dict]) -> List[Dict]:
        """
        从完整的工具调用chunk列表中提取完整的工具调用
        采用完全等待模式，确保所有参数都完整
        """
        if not tool_call_chunks:
            return []
        
        logger.info(f"开始从 {len(tool_call_chunks)} 个chunk中提取工具调用")
        
        # 按工具调用ID分组收集所有chunk
        tool_call_groups = {}
        current_tool_call_id = None
        
        for chunk_info in tool_call_chunks:
            chunk_id = chunk_info["chunk_id"]
            deltas = chunk_info["delta"]
            
            for delta in deltas:
                # 处理工具调用ID
                if delta.id:
                    # 新的工具调用开始
                    current_tool_call_id = delta.id
                    if current_tool_call_id not in tool_call_groups:
                        tool_call_groups[current_tool_call_id] = {
                            "id": current_tool_call_id,
                            "name": "",
                            "arguments": ""
                        }
                        logger.debug(f"开始收集工具调用 {current_tool_call_id} 的chunk")
                
                # 只有在有有效工具调用ID时才处理function信息
                if current_tool_call_id and delta.function:
                    if delta.function.name:
                        tool_call_groups[current_tool_call_id]["name"] = delta.function.name
                    if delta.function.arguments:
                        tool_call_groups[current_tool_call_id]["arguments"] += delta.function.arguments
        
        # 构建完整的工具调用列表
        complete_tool_calls = []
        for tool_call_id, tool_call_data in tool_call_groups.items():
            # 验证工具调用是否完整
            if tool_call_data["name"] and tool_call_data["arguments"]:
                # 验证JSON参数是否完整
                if self._is_valid_json(tool_call_data["arguments"]):
                    complete_tool_calls.append(self._finalize_tool_call(tool_call_data))
                    logger.info(f"工具调用 {tool_call_data['name']} 参数完整，长度: {len(tool_call_data['arguments'])}")
                else:
                    logger.warning(f"工具调用 {tool_call_data['name']} 参数不完整，跳过: {repr(tool_call_data['arguments'][:100])}")
            else:
                logger.warning(f"工具调用 {tool_call_id} 缺少必要信息，跳过")
        
        logger.info(f"成功提取 {len(complete_tool_calls)} 个完整工具调用")
        return complete_tool_calls

    def _is_valid_json(self, json_str: str) -> bool:
        """检查字符串是否是有效的JSON"""
        try:
            json.loads(json_str)
            return True
        except json.JSONDecodeError:
            return False
    
    def _try_fix_incomplete_json(self, json_str: str) -> str:
        """尝试修复不完整的JSON字符串"""
        if not json_str.strip():
            return json_str
            
        try:
            # 尝试直接解析
            json.loads(json_str)
            return json_str
        except json.JSONDecodeError as e:
            logger.debug(f"JSON解析失败，尝试修复: {e}")
            
            # 常见的修复策略
            fixed_str = json_str
            
            # 1. 检查未闭合的引号
            quote_count = json_str.count('"') - json_str.count('\\"')
            if quote_count % 2 != 0:
                # 找到最后一个未闭合的引号位置
                last_quote_pos = json_str.rfind('"')
                if last_quote_pos > 0:
                    # 检查是否在字符串内部
                    before_quote = json_str[:last_quote_pos]
                    if before_quote.count('"') % 2 == 0:
                        # 在字符串末尾添加引号
                        fixed_str = json_str + '"'
                        logger.debug("修复未闭合的引号")
            
            # 2. 检查未闭合的大括号
            brace_count = json_str.count('{') - json_str.count('}')
            if brace_count > 0:
                fixed_str = json_str + '}' * brace_count
                logger.debug(f"修复未闭合的大括号，添加 {brace_count} 个")
            
            # 3. 检查未闭合的方括号
            bracket_count = json_str.count('[') - json_str.count(']')
            if bracket_count > 0:
                fixed_str = json_str + ']' * bracket_count
                logger.debug(f"修复未闭合的方括号，添加 {bracket_count} 个")
            
            # 4. 尝试解析修复后的字符串
            try:
                json.loads(fixed_str)
                logger.info(f"JSON修复成功，原始长度: {len(json_str)}, 修复后长度: {len(fixed_str)}")
                return fixed_str
            except json.JSONDecodeError:
                logger.warning(f"JSON修复失败，原始字符串: {repr(json_str[:100])}")
                return json_str

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
