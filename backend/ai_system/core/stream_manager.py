"""
支持持久化的流式输出管理器
管理全程流式输出，包括XML标签格式，并支持聊天记录持久化
"""

import logging
from typing import Optional, Callable, List
from abc import ABC, abstractmethod

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


class StreamOutputManager:
    """管理全程流式输出，包括XML标签格式"""

    def __init__(self, stream_callback: Optional[StreamCallback] = None):
        self.indent_level = 0
        self.stream_callback = stream_callback
        self.output_count = 0
        self.current_message_buffer = ""
        self.current_role = "assistant"
        logger.info("StreamOutputManager初始化完成")

    async def _output(self, content: str):
        """统一的输出方法，确保实时性"""
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
                import asyncio
                await asyncio.sleep(0)
            except Exception as e:
                logger.error(f"回调函数调用失败: {e}")
        else:
            logger.debug("无回调函数，直接打印")
            print(content, end="", flush=True)

    async def print_xml_open(self, tag_name: str, content: str = ""):
        """打印XML开始标签"""
        indent = ""
        if content:
            output = f"{indent}<{tag_name}>\n{indent}{content}"
        else:
            output = f"{indent}<{tag_name}>"

        logger.debug(f"XML开始标签: {tag_name}")
        await self._output(output)
        self.indent_level += 1

    async def print_xml_close(self, tag_name: str):
        """打印XML结束标签"""
        self.indent_level -= 1
        indent = ""
        output = f"{indent}</{tag_name}>"

        logger.debug(f"XML结束标签: {tag_name}")
        await self._output(output)

    async def print_content(self, content: str):
        """打印内容"""
        indent = ""
        output = f"{indent}{content}"

        logger.debug(f"打印内容: {repr(content[:50])}...")
        await self._output(output)

    async def print_stream(self, content: str):
        """流式打印内容（不换行）"""
        logger.debug(f"流式打印: {repr(content[:50])}...")
        await self._output(content)

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


class PersistentStreamManager(StreamOutputManager):
    """支持持久化的流式输出管理器"""
    
    def __init__(self, stream_callback: Optional[StreamCallback] = None, 
                 chat_service=None, session_id: str = None):
        super().__init__(stream_callback)
        self.chat_service = chat_service
        self.session_id = session_id
        self.message_buffer = []
        self.buffer_size = 100  # 缓冲区大小
        
        if chat_service and session_id:
            logger.info(f"持久化流式管理器初始化完成，会话ID: {session_id}")
        else:
            logger.warning("持久化流式管理器未配置聊天服务或会话ID")

    async def _output(self, content: str):
        """重写输出方法，支持流式持久化"""
        # 调用父类方法进行流式输出
        await super()._output(content)
        
        # 缓冲内容，定期保存到数据库
        if len(self.current_message_buffer) > self.buffer_size or content.endswith('\n'):
            await self._save_message_buffer()
    
    async def save_user_message(self, content: str):
        """专门保存用户消息的方法"""
        if self.chat_service and self.session_id:
            try:
                # 从session_id中提取work_id
                work_id = self.session_id.replace("_main_session", "") if "_main_session" in self.session_id else self.session_id
                
                # 在事件循环中运行同步方法
                import asyncio
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    self.chat_service.add_message,
                    work_id,
                    "user",
                    content,
                    None  # metadata
                )
                logger.info(f"[PERSISTENCE] 用户消息持久化完成，work_id: {work_id}, 长度: {len(content)}")
            except Exception as e:
                logger.error(f"用户消息持久化失败: {e}")
        else:
            logger.warning("无法持久化用户消息：聊天服务或会话ID未配置")
    
    async def _save_message_buffer(self):
        """保存消息缓冲到数据库"""
        if self.chat_service and self.session_id and self.current_message_buffer.strip():
            try:
                # 检查聊天服务是否有add_message方法
                if hasattr(self.chat_service, 'add_message'):
                    # ChatService.add_message是同步方法，需要从session_id中提取work_id
                    work_id = self.session_id.replace("_main_session", "") if "_main_session" in self.session_id else self.session_id
                    
                    # 在事件循环中运行同步方法
                    import asyncio
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None,
                        self.chat_service.add_message,
                        work_id,
                        self.current_role,
                        self.current_message_buffer.strip(),
                        None  # metadata
                    )
                    logger.info(f"[PERSISTENCE] 缓冲消息已保存，work_id: {work_id}, 角色: {self.current_role}, 长度: {len(self.current_message_buffer)}")
                    
                    # 清空已保存的缓冲区内容
                    self.current_message_buffer = ""
                else:
                    logger.warning("聊天服务没有add_message方法")
            except Exception as e:
                logger.error(f"保存消息缓冲失败: {e}")
    
    async def finalize_message(self):
        """完成消息，保存剩余内容"""
        # 先保存缓冲的内容
        if self.chat_service and self.session_id and self.current_message_buffer.strip():
            try:
                # 检查聊天服务是否有add_message方法
                if hasattr(self.chat_service, 'add_message'):
                    # ChatService.add_message是同步方法，需要从session_id中提取work_id
                    work_id = self.session_id.replace("_main_session", "") if "_main_session" in self.session_id else self.session_id
                    
                    # 在事件循环中运行同步方法
                    import asyncio
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None,
                        self.chat_service.add_message,
                        work_id,
                        self.current_role,
                        self.current_message_buffer.strip(),
                        None  # metadata
                    )
                    logger.info(f"[PERSISTENCE] 消息持久化完成，work_id: {work_id}, 角色: {self.current_role}, 长度: {len(self.current_message_buffer)}")
                else:
                    logger.warning("聊天服务没有add_message方法")
            except Exception as e:
                logger.error(f"消息持久化失败: {e}")
                # 记录更多调试信息
                logger.error(f"聊天服务类型: {type(self.chat_service)}")
                logger.error(f"会话ID: {self.session_id}")
                logger.error(f"消息角色: {self.current_role}")
                logger.error(f"消息内容长度: {len(self.current_message_buffer)}")
        else:
            logger.warning(f"无法持久化消息: chat_service={self.chat_service is not None}, session_id={self.session_id}, content_length={len(self.current_message_buffer)}")
        
        # 调用父类方法
        await super().finalize_message()
        
        # 清空缓冲区
        self.current_message_buffer = ""


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


class CodeAgentStreamManager(StreamOutputManager):
    """CodeAgent专用的StreamManager，将输出转发到MainAgent但保持消息隔离"""
    
    def __init__(self, main_stream_manager: StreamOutputManager, agent_name: str):
        super().__init__()
        self.main_stream_manager = main_stream_manager
        self.agent_name = agent_name
        self.output_buffer = []
        self.is_forwarding = True
        
        logger.info(f"CodeAgentStreamManager初始化完成，代理名称: {agent_name}")
        
    async def _output(self, content: str):
        """重写输出方法，转发到MainAgent的StreamManager"""
        # 缓冲内容
        self.output_buffer.append(content)
        
        # 转发到MainAgent的StreamManager，添加标识前缀
        if self.main_stream_manager and self.is_forwarding:
            try:
                # 使用特殊的XML标签标识这是CodeAgent的输出
                await self.main_stream_manager.print_xml_open(f"code_agent_output")
                await self.main_stream_manager.print_content(f"[{self.agent_name}] {content}")
                await self.main_stream_manager.print_xml_close(f"code_agent_output")
            except Exception as e:
                logger.error(f"转发CodeAgent输出失败: {e}")
    
    async def print_xml_open(self, tag_name: str, content: str = ""):
        """转发XML开始标签"""
        if self.main_stream_manager and self.is_forwarding:
            try:
                # 添加CodeAgent标识
                await self.main_stream_manager.print_xml_open(f"code_agent_{tag_name}")
                if content:
                    await self.main_stream_manager.print_content(f"[{self.agent_name}] {content}")
            except Exception as e:
                logger.error(f"转发CodeAgent XML标签失败: {e}")
    
    async def print_xml_close(self, tag_name: str):
        """转发XML结束标签"""
        if self.main_stream_manager and self.is_forwarding:
            try:
                await self.main_stream_manager.print_xml_close(f"code_agent_{tag_name}")
            except Exception as e:
                logger.error(f"转发CodeAgent XML标签失败: {e}")
    
    async def print_content(self, content: str):
        """转发内容"""
        await self._output(content)
    
    async def print_stream(self, content: str):
        """流式打印内容（不换行）"""
        await self._output(content)
    
    def set_forwarding(self, enabled: bool):
        """设置是否启用转发"""
        self.is_forwarding = enabled
        logger.debug(f"CodeAgent转发状态设置为: {enabled}")
    
    async def finalize_message(self):
        """完成消息，清理缓冲区"""
        self.output_buffer.clear()
        await super().finalize_message()
        
        # 发送CodeAgent完成标识
        if self.main_stream_manager and self.is_forwarding:
            try:
                await self.main_stream_manager.print_xml_open("code_agent_complete")
                await self.main_stream_manager.print_content(f"[{self.agent_name}] 任务执行完成")
                await self.main_stream_manager.print_xml_close("code_agent_complete")
            except Exception as e:
                logger.error(f"发送CodeAgent完成标识失败: {e}")