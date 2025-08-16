"""
主AI代理
主LLM Agent (Orchestrator)，负责分析问题并委派任务
"""

import logging
import json
from typing import List, Dict, Any

from .agents import Agent
from .llm_handler import LLMHandler
from .stream_manager import StreamOutputManager
from ..tools.file_tools import FileTools

logger = logging.getLogger(__name__)


class MainAgent(Agent):
    """
    主 LLM Agent (Orchestrator)，负责分析问题并委派任务。
    """

    def __init__(self, llm_handler: LLMHandler, stream_manager: StreamOutputManager):
        super().__init__(llm_handler, stream_manager)
        self.file_tools = FileTools(stream_manager)
        self._setup()
        logger.info("MainAgent初始化完成")

    def _setup(self):
        """初始化 System Prompt 和工具。"""
        self.messages = [{
            "role": "system",
            "content": (
                "你是一个建模专家，擅长将用户的问题转化为数学模型。"
                "分析用户问题，如果涉及具体计算、数据分析"
                "必须交给 CodeAgent 工具来完成。"
                "例如你可以调用 CodeAgent 工具，请生成这份数据的可视化图片"
                "或者，请编程计算这个微分方程的解"
                "任务完成后，必须先使用tree工具查看目录结构，确认所有生成的文件都存在，"
                "然后使用writemd工具生成最终的论文文档。在论文中要引用生成的文件。"
            )
        }]

        # 注册工具
        code_interpreter_tool = {
            "type": "function",
            "function": {
                "name": "CodeAgent",
                "description": "当需要数学计算、数据分析或执行编程任务时调用。提供清晰、具体的任务描述。不要提供代码。",
                "parameters": {
                    "type": "object",
                    "properties": {"task_prompt": {"type": "string", "description": "需要执行的具体任务描述。不要提供代码。"}},
                    "required": ["task_prompt"],
                },
            },
        }

        writemd_tool = {
            "type": "function",
            "function": {
                "name": "writemd",
                "description": "将内容写入Markdown文件到workspace目录",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "文件名（不需要.md后缀）"},
                        "content": {"type": "string", "description": "Markdown格式的内容"}
                    },
                    "required": ["filename", "content"],
                },
            },
        }

        tree_tool = {
            "type": "function",
            "function": {
                "name": "tree",
                "description": "显示workspace目录的树形结构",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "要显示的目录路径，默认为workspace目录"}
                    },
                    "required": [],
                },
            },
        }

        self.tools.extend([code_interpreter_tool, writemd_tool, tree_tool])
        self.available_functions.update({
            "writemd": self.file_tools.writemd,
            "tree": self.file_tools.tree
        })

    async def run(self, user_problem: str):
        """执行主 Agent 逻辑，循环处理直到任务完成。"""
        logger.info(f"MainAgent开始执行，问题长度: {len(user_problem)} 字符")

        if self.stream_manager:
            await self.stream_manager.print_xml_open("main_agent")

        self.messages.append({"role": "user", "content": user_problem})

        iteration_count = 0
        while True:
            iteration_count += 1
            logger.info(f"MainAgent第 {iteration_count} 次迭代")

            assistant_message, tool_calls = await self.llm_handler.process_stream(
                self.messages, self.tools)
            self.messages.append(assistant_message)

            if not tool_calls:
                logger.info("MainAgent没有工具调用，任务完成")
                if self.stream_manager:
                    await self.stream_manager.print_xml_close("main_agent")
                break

            logger.info(f"MainAgent执行 {len(tool_calls)} 个工具调用")

            # 处理所有工具调用
            for i, tool_call in enumerate(tool_calls):
                function_name = tool_call["function"]["name"]
                logger.info(f"处理工具调用 {i+1}/{len(tool_calls)}: {function_name}")

                if function_name == "CodeAgent":
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                        task_prompt = args.get("task_prompt", "")

                        if self.stream_manager:
                            await self.stream_manager.print_xml_open(
                                "call_code_agent")
                            await self.stream_manager.print_content(task_prompt)
                            await self.stream_manager.print_xml_close(
                                "call_code_agent")

                        # 创建并运行子 Agent
                        from .agents import CodeAgent
                        code_agent = CodeAgent(
                            self.llm_handler, self.stream_manager)
                        tool_result = await code_agent.run(task_prompt)

                        # 将子 Agent 的结果添加回主 Agent 的消息历史
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": tool_result,
                        })
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON解析失败: {e}")
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": f"工具参数解析失败: {e}",
                        })

                elif function_name in self.available_functions:
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                        tool_result = await self.available_functions[function_name](
                            **args)

                        # 将工具执行结果添加回消息历史
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": tool_result,
                        })
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON解析失败: {e}")
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": f"工具参数解析失败: {e}",
                        })

        logger.info(f"MainAgent执行完成，总共 {iteration_count} 次迭代")

    def get_execution_summary(self) -> Dict[str, Any]:
        """获取执行摘要"""
        return {
            "total_messages": len(self.messages),
            "tool_calls_count": sum(1 for msg in self.messages if msg.get("role") == "tool"),
            "user_messages": sum(1 for msg in self.messages if msg.get("role") == "user"),
            "assistant_messages": sum(1 for msg in self.messages if msg.get("role") == "assistant"),
            "workspace_files": self.file_tools.list_files()
        }

    def reset_conversation(self):
        """重置对话历史"""
        self.messages = [self.messages[0]]  # 保留system message
        logger.info("MainAgent对话历史已重置")

    def export_conversation(self) -> List[Dict[str, Any]]:
        """导出对话历史"""
        return self.messages.copy()
