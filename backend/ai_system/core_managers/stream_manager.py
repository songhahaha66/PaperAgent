"""
流式输出适配器；消息持久化由会话时间线负责
"""

import logging
from typing import Optional, Callable, List, Dict, Any
from abc import ABC, abstractmethod
import json
import asyncio

logger = logging.getLogger(__name__)


class StreamCallback(ABC):
    """流式输出回调接口"""

    @abstractmethod
    async def on_content(self, content: str):
        """处理流式内容"""
        pass

    @abstractmethod
    async def on_message_complete(self, role: str, content: str):
        """消息完成时的回调"""
        pass

    @abstractmethod
    async def on_json_block(self, block: Dict[str, Any]):
        """处理JSON格式的数据块"""
        pass


class StreamOutputManager:
    """管理全程流式输出，使用JSON格式"""

    def __init__(self, stream_callback: Optional[StreamCallback] = None):
        self.stream_callback = stream_callback
        self.output_count = 0
        self.current_message_buffer = ""
        self.current_role = "assistant"
        self.current_block_type = "main"
        # 添加异步锁，防止并发输出问题
        self._output_lock = asyncio.Lock()
        logger.info("StreamOutputManager初始化完成")

    async def _output(self, content: str):
        """统一的输出方法，确保实时性和非阻塞性"""
        async with self._output_lock:
            self.output_count += 1
            logger.debug(
                f"StreamOutputManager._output() 第 {self.output_count} 次调用: {repr(content[:50])}...")

            # 缓冲内容
            self.current_message_buffer += content

            if self.stream_callback:
                try:
                    # 立即调用回调函数，实现实时流式传输
                    await self.stream_callback.on_content(content)
                    logger.debug(f"成功调用回调函数，内容长度: {len(content)}")

                    # 让出控制权，确保事件循环能处理其他任务
                    await asyncio.sleep(0.001)
                except Exception as e:
                    logger.error(f"回调函数调用失败: {e}")
            else:
                logger.debug("无回调函数，直接打印")
                print(content, end="", flush=True)

    async def send_json_block(self, block_type: str, content: Any):
        """发送JSON格式的数据块（content 可以是字符串，也可以是 plan_updated 这类结构化数据）"""
        block = {
            "type": block_type,
            "content": content
        }

        logger.debug(f"发送JSON块: {block_type} - {repr(str(content)[:50])}...")

        if self.stream_callback:
            try:
                await self.stream_callback.on_json_block(block)
                logger.debug(f"成功发送JSON块: {block_type}")

                # 让出控制权
                await asyncio.sleep(0.001)
            except Exception as e:
                logger.error(f"发送JSON块失败: {e}")
        else:
            # 直接打印JSON格式
            print(json.dumps(block, ensure_ascii=False), flush=True)

    async def finalize_message(self):
        """完成当前消息，触发完成回调"""
        if self.stream_callback and self.current_message_buffer.strip():
            try:
                await self.stream_callback.on_message_complete(
                    self.current_role,
                    self.current_message_buffer.strip()
                )
                logger.debug("消息完成回调执行成功")
            except Exception as e:
                logger.error(f"消息完成回调执行失败: {e}")

            # 清空缓冲区
            self.current_message_buffer = ""

    def set_role(self, role: str):
        """设置当前消息的角色"""
        self.current_role = role
        logger.debug(f"设置消息角色: {role}")


class SimpleStreamCallback(StreamCallback):
    """简单的流式输出回调实现"""

    def __init__(self, output_queue=None):
        self.output_queue = output_queue

    async def on_content(self, content: str):
        """处理流式内容"""
        if self.output_queue:
            try:
                await self.output_queue.put(content)
            except Exception as e:
                logger.error(f"输出队列写入失败: {e}")

    async def on_message_complete(self, role: str, content: str):
        """消息完成时的回调"""
        logger.info(f"消息完成，角色: {role}, 长度: {len(content)}")
        if self.output_queue:
            try:
                # 发送完成标记
                await self.output_queue.put("[MESSAGE_COMPLETE]")
            except Exception as e:
                logger.error(f"发送完成标记失败: {e}")

    async def on_json_block(self, block: Dict[str, Any]):
        """处理JSON格式的数据块"""
        if self.output_queue:
            try:
                await self.output_queue.put(json.dumps(block, ensure_ascii=False))
            except Exception as e:
                logger.error(f"JSON块队列写入失败: {e}")
