"""
AI代理系统
包含Agent基类、CodeAgent等核心代理类
"""

import logging
import json
from typing import List, Dict, Any, Callable
from abc import ABC, abstractmethod

from .llm_handler import LLMHandler
from .stream_manager import StreamOutputManager
from ..tools.code_executor import CodeExecutor

logger = logging.getLogger(__name__)


class Agent(ABC):
    """Agent 的基类，定义通用接口。"""

    def __init__(self, llm_handler: LLMHandler, stream_manager: StreamOutputManager = None):
        self.llm_handler = llm_handler
        self.stream_manager = stream_manager
        self.messages: List[Dict[str, Any]] = []
        self.tools: List[Dict[str, Any]] = []
        self.available_functions: Dict[str, Callable] = {}

    def _register_tool(self, func: Callable, tool_definition: Dict):
        """注册一个工具及其实现函数。"""
        self.tools.append(tool_definition)
        self.available_functions[func.__name__] = func
        logger.debug(f"注册工具: {func.__name__}")

    @abstractmethod
    async def run(self, *args, **kwargs):
        """每个 Agent 子类必须实现 run 方法。"""
        raise NotImplementedError("每个 Agent 子类必须实现 run 方法。")


class CodeAgent(Agent):
    """
    代码手 LLM Agent，负责生成和执行代码。
    """

    def __init__(self, llm_handler: LLMHandler, stream_manager: StreamOutputManager):
        super().__init__(llm_handler, stream_manager)
        self.executor = CodeExecutor(stream_manager)
        self._setup()
        logger.info("CodeAgent初始化完成")

    def _setup(self):
        """初始化 System Prompt 和工具。"""
        self.messages = [{
            "role": "system",
            "content": (
                "根据用户的任务描述，先规划分析代码结构，然后使用 execute_code_file 工具来执行代码文件。"
                "**也就是说当你想开始写代码时，就调用 execute_code_file 工具，execute_code_file的输入就是代码文件路径**"
                "必须使用 execute_code_file 工具来执行代码，并根据执行结果进行总结。"
                "**注意：如果需要保存图像或数据文件，请使用workspace_dir=os.environ[\"WORKSPACE_DIR\"]变量指向的路径。**"
                "例如：plt.savefig(os.path.join(workspace_dir,'outputs/plots/figure.png'))"
                "保存图像后，请显示保存路径，确保用户知道文件保存位置。"
                "代码执行结果会自动保存到execution_logs目录，图片会自动保存到outputs/plots目录。"
            )
        }]
        
        # 新的工具定义
        execute_code_tool = {
            "type": "function",
            "function": {
                "name": "execute_code_file",
                "description": "执行指定的Python代码文件并返回结果",
                "parameters": {
                    "type": "object", 
                    "properties": {
                        "code_file_path": {
                            "type": "string", 
                            "description": "要执行的代码文件路径"
                        }
                    },
                    "required": ["code_file_path"],
                },
            },
        }
        
        # 注册工具
        self._register_tool(self.executor.execute_code_file, execute_code_tool)

    async def run(self, task_prompt: str) -> str:
        """执行代码生成和解释任务，支持多次代码执行。"""
        logger.info(f"CodeAgent开始执行任务: {repr(task_prompt[:50])}...")

        if self.stream_manager:
            await self.stream_manager.print_xml_open("ret_code_agent")

        self.messages.append({"role": "user", "content": task_prompt})

        max_iterations = 5  # 最大迭代次数，防止无限循环
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            logger.info(f"CodeAgent第{iteration}次迭代")

            # 调用LLM生成代码或分析结果
            assistant_message, tool_calls = await self.llm_handler.process_stream(
                self.messages, self.tools)
            self.messages.append(assistant_message)

            if not tool_calls:
                # 没有工具调用，说明LLM认为任务完成，生成最终回答
                result = assistant_message.get("content", "代码手任务完成。")
                logger.info(f"CodeAgent在第{iteration}次迭代完成，无更多工具调用")
                if self.stream_manager:
                    await self.stream_manager.print_xml_close("ret_code_agent")
                return result

            # 执行所有工具调用
            for tool_call in tool_calls:
                function_name = tool_call["function"]["name"]
                logger.info(f"CodeAgent执行工具调用: {function_name}")

                if function_name in self.available_functions:
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                        result = await self.available_functions[function_name](
                            **args)

                        # 将工具执行结果添加回消息历史
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": result,
                        })

                        logger.info(
                            f"工具 {function_name} 执行成功，结果长度: {len(result)} 字符")

                    except json.JSONDecodeError as e:
                        logger.error(f"JSON解析失败: {e}")
                        result = f"代码手LLM处理失败：JSON解析错误 - {str(e)}"
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": result,
                        })
                else:
                    logger.warning(f"未知工具: {function_name}")
                    result = f"未知工具: {function_name}"
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    })

        # 达到最大迭代次数
        logger.warning(f"CodeAgent达到最大迭代次数({max_iterations})，强制结束")
        if self.stream_manager:
            await self.stream_manager.print_content(
                f"达到最大迭代次数({max_iterations})，任务结束")
            await self.stream_manager.print_xml_close("ret_code_agent")

        return "代码手任务完成（达到最大迭代次数）"

    def get_execution_stats(self) -> Dict[str, Any]:
        """获取执行统计信息"""
        return {
            "total_messages": len(self.messages),
            "tool_calls_count": sum(1 for msg in self.messages if msg.get("role") == "tool"),
            "workspace_files": self.executor.list_workspace_files()
        }
