"""
增强的AI代理基类系统
提供统一的代理架构，包含工具管理、消息处理、执行框架等通用功能
"""

import logging
import json
import asyncio
from typing import List, Dict, Any, Callable, Optional
from abc import ABC, abstractmethod
from datetime import datetime

from ..core_managers.tool_manager import ToolManager
from ..core_managers.context_manager import ContextManager

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    增强的Agent基类，提供统一的代理架构和通用功能
    """

    def __init__(self, llm_handler: 'LLMHandler', stream_manager: 'StreamOutputManager' = None,
                 workspace_dir: Optional[str] = None, work_id: Optional[str] = None):
        """
        初始化基础Agent

        Args:
            llm_handler: LLM处理器
            stream_manager: 流式输出管理器
            workspace_dir: 工作空间目录
            work_id: 工作ID
        """
        self.llm_handler = llm_handler
        self.stream_manager = stream_manager
        self.work_id = work_id
        self.workspace_dir = workspace_dir

        # 初始化工具管理器
        if workspace_dir:
            self.tool_manager = ToolManager(workspace_dir, stream_manager)
        else:
            self.tool_manager = None

        # 消息和工具管理
        self.messages: List[Dict[str, Any]] = []
        self.tools: List[Dict[str, Any]] = []
        self.available_functions: Dict[str, Callable] = {}

        # 上下文管理
        self.context_manager = ContextManager(
            max_tokens=20000,
            max_messages=50
        )

        # 初始化系统设置
        self._initialize()
        logger.info(f"{self.__class__.__name__}初始化完成，work_id: {work_id}")

    def _initialize(self):
        """初始化代理的通用设置"""
        # 设置系统消息
        system_prompt = self.get_system_prompt()
        self.messages = [{
            "role": "system",
            "content": system_prompt
        }]

        # 设置工具
        self._setup_tools()

        # 注册工具函数
        self._register_tool_functions()

    @abstractmethod
    def get_system_prompt(self) -> str:
        """
        获取系统提示词，子类必须实现

        Returns:
            系统提示词
        """
        pass

    @abstractmethod
    def _setup_tools(self):
        """
        设置工具定义，子类必须实现
        定义该Agent需要的工具及其参数结构
        """
        pass

    @abstractmethod
    async def run(self, *args, **kwargs):
        """
        执行Agent的主要逻辑，子类必须实现

        Args:
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            执行结果
        """
        pass

    def _register_tool_functions(self):
        """
        注册工具函数到available_functions字典
        子类可以重写此方法来注册自定义工具函数
        """
        # 默认不注册任何函数，由子类实现
        pass

    def register_tool(self, func: Callable, tool_definition: Dict[str, Any]):
        """
        注册工具及其实现函数

        Args:
            func: 工具实现函数
            tool_definition: 工具定义字典
        """
        self.tools.append(tool_definition)
        tool_name = tool_definition["function"]["name"]
        self.available_functions[tool_name] = func
        logger.debug(f"注册工具: {tool_name} -> {func.__name__}")

    async def _execute_tool_call(self, tool_call: Dict[str, Any], index: int = 1, total: int = 1) -> str:
        """
        执行单个工具调用的通用方法

        Args:
            tool_call: 工具调用字典
            index: 当前工具调用索引
            total: 总工具调用数量

        Returns:
            工具执行结果
        """
        import json

        function_name = tool_call["function"]["name"]
        logger.info(f"执行工具调用 {index}/{total}: {function_name}")

        try:
            # 解析参数
            args = json.loads(tool_call["function"]["arguments"])

            # 发送工具调用开始通知
            if self.stream_manager:
                try:
                    await self.stream_manager.print_main_content(f"{self.__class__.__name__}正在执行工具: {function_name}")
                except Exception as e:
                    logger.warning(f"发送工具调用通知失败: {e}")

            # 执行工具函数
            if function_name in self.available_functions:
                func = self.available_functions[function_name]
                if asyncio.iscoroutinefunction(func):
                    tool_result = await func(**args)
                else:
                    # 同步函数在线程池中执行
                    loop = asyncio.get_event_loop()
                    tool_result = await loop.run_in_executor(None, lambda: func(**args))

                # 发送工具调用完成通知
                if self.stream_manager:
                    try:
                        await self.stream_manager.print_main_content(f"工具 {function_name} 执行完成，结果长度: {len(tool_result)} 字符")
                    except Exception as e:
                        logger.warning(f"发送工具完成通知失败: {e}")

                return tool_result
            else:
                # 处理未知工具调用
                logger.warning(f"未知工具: {function_name}")
                return f"未知工具: {function_name}"

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            return f"工具参数解析失败: {str(e)}"
        except Exception as e:
            logger.error(f"工具 {function_name} 执行失败: {e}")
            return f"工具执行失败: {str(e)}"

    async def _execute_code_agent(self, task_prompt: str, tool_call_id: str) -> str:
        """
        执行CodeAgent的通用方法

        Args:
            task_prompt: 任务提示词
            tool_call_id: 工具调用ID

        Returns:
            CodeAgent执行结果
        """
        try:
            # 避免循环导入，直接引用当前文件中的CodeAgent类
            CodeAgent = globals()['CodeAgent']

            # 创建独立的StreamManager给CodeAgent使用
            from ..core_managers.stream_manager import CodeAgentStreamManager
            code_agent_stream = CodeAgentStreamManager(
                main_stream_manager=self.stream_manager,
                agent_name="CodeAgent"
            )

            # 创建CodeAgent实例
            code_agent = CodeAgent(
                self.llm_handler,
                code_agent_stream,
                self.workspace_dir,
                self.work_id
            )

            # 执行任务
            result = await code_agent.run(task_prompt)
            logger.info(f"CodeAgent执行完成，工具调用ID: {tool_call_id}, 结果长度: {len(result)}")

            return result

        except Exception as e:
            error_msg = f"CodeAgent执行失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    def add_user_message(self, message: str) -> bool:
        """
        添加用户消息到对话历史

        Args:
            message: 用户消息

        Returns:
            是否成功添加（False表示重复消息）
        """
        # 检查重复消息
        existing_user_messages = [
            msg for msg in self.messages if msg.get('role') == 'user'
        ]
        is_duplicate = any(
            msg.get('content') == message for msg in existing_user_messages
        )

        if is_duplicate:
            logger.warning(f"检测到重复的用户消息，跳过添加: {message[:50]}...")
            return False

        self.messages.append({"role": "user", "content": message})
        logger.info("用户消息已添加到对话历史")
        return True

    def _check_and_compress_context(self):
        """检查并压缩上下文"""
        context_status = self.context_manager.get_context_status(self.messages)

        if context_status["compression_needed"]:
            logger.info(f"上下文过长，开始压缩。当前token使用率: {context_status['token_usage_ratio']:.2%}")

            # 选择压缩策略
            if context_status["token_usage_ratio"] > 0.8:
                strategy = "high"
            elif context_status["token_usage_ratio"] > 0.6:
                strategy = "medium"
            else:
                strategy = "low"

            # 执行压缩
            compressed_messages, compression_results = self.context_manager.compress_context(
                self.messages, strategy
            )

            # 更新消息列表
            self.messages = compressed_messages
            self.context_manager.compression_history.extend(compression_results)

            logger.info(f"上下文压缩完成，压缩后消息数: {len(self.messages)}")

    def load_conversation_history(self, history_messages: List[Dict[str, Any]]):
        """
        加载对话历史，维护上下文连续性

        Args:
            history_messages: 历史消息列表
        """
        if not history_messages:
            return

        # 保留system message，添加历史消息
        system_message = self.messages[0]
        self.messages = [system_message]

        # 添加历史消息，过滤tool消息
        for msg in history_messages:
            if msg.get('role') in ["user", "assistant"]:
                self.messages.append(msg)

        # 检查是否需要压缩上下文
        self._check_and_compress_context()

        logger.info(f"已加载 {len(self.messages) - 1} 条历史消息，维护上下文连续性")

    def reset_conversation(self):
        """重置对话历史"""
        self.messages = [self.messages[0]]  # 只保留system message
        logger.info(f"{self.__class__.__name__}对话历史已重置")

    def export_conversation(self) -> List[Dict[str, Any]]:
        """导出对话历史"""
        return self.messages.copy()

    def get_context_summary(self) -> str:
        """获取上下文摘要"""
        if len(self.messages) <= 1:
            return "对话刚开始，暂无上下文"

        try:
            summary = self.context_manager._format_summary_content(
                self.messages,
                self.context_manager.extract_key_topics(self.messages),
                self.context_manager._extract_important_points(self.messages)
            )
            return summary
        except Exception as e:
            logger.error(f"生成上下文摘要失败: {e}")
            return self._get_simple_context_summary()

    def _get_simple_context_summary(self) -> str:
        """获取简单的上下文摘要（回退方法）"""
        user_count = sum(1 for msg in self.messages if msg.get("role") == "user")
        ai_count = sum(1 for msg in self.messages if msg.get("role") == "assistant")

        recent_questions = []
        for msg in reversed(self.messages):
            if msg.get("role") == "user" and len(recent_questions) < 3:
                recent_questions.append(msg.get("content", "")[:100])

        summary = f"当前对话已进行 {user_count} 轮交互，用户提问 {user_count} 次，AI回答 {ai_count} 次。"
        if recent_questions:
            summary += f" 最近的用户问题：{' | '.join(reversed(recent_questions))}"

        return summary

    def get_execution_summary(self) -> Dict[str, Any]:
        """获取执行摘要"""
        context_status = self.context_manager.get_context_status(self.messages)

        return {
            "total_messages": len(self.messages),
            "tool_calls_count": sum(1 for msg in self.messages if msg.get("role") == "tool"),
            "user_messages": sum(1 for msg in self.messages if msg.get("role") == "user"),
            "assistant_messages": sum(1 for msg in self.messages if msg.get("role") == "assistant"),
            "work_id": self.work_id,
            "agent_type": self.__class__.__name__,
            "context_status": context_status
        }


class CodeAgent(BaseAgent):
    """
    代码生成和执行Agent，专注于代码相关任务
    """

    def __init__(self, llm_handler: 'LLMHandler', stream_manager: 'StreamOutputManager', workspace_dir: str, work_id: Optional[str] = None):
        """
        初始化CodeAgent

        Args:
            llm_handler: LLM处理器
            stream_manager: 流式输出管理器
            workspace_dir: 工作空间目录（必需）
            work_id: 工作ID
        """
        if not workspace_dir:
            raise ValueError("CodeAgent必须提供workspace_dir参数")

        super().__init__(llm_handler, stream_manager, workspace_dir, work_id)

    def get_system_prompt(self) -> str:
        """获取CodeAgent的系统提示词"""
        return (
            "你是一个专业的代码生成和执行助手。**务必确保成功产出所需文件再交付**，工作完成之前一定要调用工具！重复执行直到成功。\n\n"
            "你的工作流程：\n"
            "1. 分析用户任务，生成相应的Python代码\n"
            "2. 使用 save_and_execute 工具保存代码并立即执行\n"
            "3. 仔细分析执行结果和错误信息\n"
            "4. 如果代码有错误或需要优化，使用 edit_code_file 修改后重新执行\n"
            "5. 重复修改和执行，直到得到正确结果\n\n"
            "**重要策略：**\n"
            "- 推荐使用 save_and_execute 工具，一次性完成保存和执行\n"
            "- 代码执行失败时，仔细分析错误信息，然后修改代码重试\n"
            "- 代码应该包含必要的导入语句和完整的逻辑\n"
            "- 文件操作使用相对路径，例如：plt.savefig('outputs/filename.png')\n"
            "- 必须要保证保存图片且图片名字含有时间戳，否则会覆盖之前的图片\n\n"
            "**重复执行直到成功**"
        )

    def _setup_tools(self):
        """设置CodeAgent的工具定义"""
        # 保存并执行代码工具
        save_and_execute_tool = {
            "type": "function",
            "function": {
                "name": "save_and_execute",
                "description": "保存Python代码到文件并立即执行",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code_content": {"type": "string", "description": "要保存和执行的Python代码内容"},
                        "filename": {"type": "string", "description": "文件名（不需要.py后缀）"}
                    },
                    "required": ["code_content", "filename"],
                },
            },
        }

        # 直接执行代码工具
        execute_code_tool = {
            "type": "function",
            "function": {
                "name": "execute_code",
                "description": "直接执行Python代码内容，不保存到文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code_content": {"type": "string", "description": "要执行的Python代码内容"}
                    },
                    "required": ["code_content"],
                },
            },
        }

        # 执行文件工具
        execute_file_tool = {
            "type": "function",
            "function": {
                "name": "execute_file",
                "description": "执行指定的Python代码文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "要执行的代码文件路径"}
                    },
                    "required": ["file_path"],
                },
            },
        }

        # 修改代码文件工具
        edit_code_tool = {
            "type": "function",
            "function": {
                "name": "edit_code_file",
                "description": "修改已存在的Python代码文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "要修改的文件名（不需要.py后缀）"},
                        "new_code_content": {"type": "string", "description": "修复后的完整代码内容"}
                    },
                    "required": ["filename", "new_code_content"],
                },
            },
        }

        # 列出代码文件工具
        list_files_tool = {
            "type": "function",
            "function": {
                "name": "list_code_files",
                "description": "列出工作空间中的所有代码文件",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

        # 注册工具定义
        self.tools = [
            save_and_execute_tool,
            execute_code_tool,
            execute_file_tool,
            edit_code_tool,
            list_files_tool
        ]

    def _register_tool_functions(self):
        """注册CodeAgent的工具函数"""
        if not self.tool_manager:
            raise ValueError("CodeAgent需要工具管理器")

        code_executor = self.tool_manager.code_executor()

        self.available_functions = {
            "save_and_execute": code_executor.save_and_execute,
            "execute_code": code_executor.execute_code,
            "execute_file": code_executor.execute_file,
            "edit_code_file": code_executor.edit_code_file,
            "list_code_files": code_executor.list_code_files
        }

    async def run(self, task_prompt: str) -> str:
        """执行代码生成和执行任务"""
        logger.info(f"CodeAgent开始执行任务: {repr(task_prompt[:50])}...")

        if self.stream_manager:
            await self.stream_manager.send_json_block("code_agent_start", f"开始执行代码任务: {task_prompt[:100]}...")

        self.add_user_message(task_prompt)

        max_iterations = 10
        iteration = 0
        last_tool_result = None

        while iteration < max_iterations:
            iteration += 1
            logger.info(f"CodeAgent第{iteration}次迭代")

            # 调用LLM
            assistant_message, tool_calls = await self.llm_handler.process_stream(self.messages, self.tools)
            self.messages.append(assistant_message)

            if not tool_calls:
                # 任务完成
                if last_tool_result:
                    result = f"任务完成！\n\n执行结果：\n{last_tool_result}\n\nLLM总结：{assistant_message.get('content', '')}"
                else:
                    result = assistant_message.get("content", "代码任务完成。")

                logger.info(f"CodeAgent在第{iteration}次迭代完成")
                if self.stream_manager:
                    await self.stream_manager.send_json_block("code_agent_result", f"任务完成，最终结果: {result[:200]}...")
                return result

            # 执行工具调用 - 使用统一的执行方法
            for tool_call in tool_calls:
                function_name = tool_call["function"]["name"]
                logger.info(f"CodeAgent执行工具调用: {function_name}")

                try:
                    tool_result = await self._execute_tool_call(tool_call)
                    last_tool_result = tool_result

                    # 添加工具结果到消息历史
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": tool_result,
                    })

                except Exception as e:
                    logger.error(f"工具 {function_name} 执行失败: {e}")
                    error_result = f"工具 {function_name} 执行失败: {str(e)}"
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": error_result,
                    })
                    last_tool_result = error_result

        # 达到最大迭代次数
        logger.warning(f"CodeAgent达到最大迭代次数({max_iterations})，强制结束")
        if last_tool_result:
            return f"任务完成（达到最大迭代次数）！\n\n最终执行结果：\n{last_tool_result}"
        else:
            return "代码任务完成（达到最大迭代次数），但未获得有效结果"