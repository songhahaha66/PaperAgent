"""
LangChain 版本的 CodeAgent
负责代码生成、执行和迭代调试，基于 CodeExecutor 工具集
"""

import logging
from typing import Any, Dict, Optional

from langchain.agents import create_agent
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from ..core_tools.code_executor import CodeExecutor

logger = logging.getLogger(__name__)


class CodeAgent:
    """
    基于 LangChain 的代码 Agent，使用 CodeExecutor 提供的工具完成代码相关任务
    """

    def __init__(
        self,
        llm: BaseLanguageModel,
        stream_manager=None,
        workspace_dir: str = "",
        work_id: Optional[str] = None,
    ):
        if not workspace_dir:
            raise ValueError("CodeAgent必须提供workspace_dir参数")
        if llm is None:
            raise ValueError("CodeAgent需要有效的LLM实例")

        self.llm = llm
        self.stream_manager = stream_manager
        self.workspace_dir = workspace_dir
        self.work_id = work_id

        self.code_executor = CodeExecutor(stream_manager=stream_manager, workspace_dir=workspace_dir)
        self.tools = self._create_tools()
        self.agent = create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=self.get_system_prompt(),
        )

        logger.info(
            "CodeAgent初始化完成，workspace_dir: %s, work_id: %s, tools: %d",
            workspace_dir,
            work_id,
            len(self.tools),
        )

    def _create_tools(self):
        """将 CodeExecutor 的能力封装为 LangChain 工具"""
        return [
            StructuredTool.from_function(
                coroutine=self.code_executor.save_and_execute,
                name="save_and_execute",
                description="保存Python代码到文件并立即执行，适合需要生成图表或日志的任务",
            ),
            StructuredTool.from_function(
                coroutine=self.code_executor.execute_code,
                name="execute_code",
                description="直接执行Python代码字符串，不落盘",
            ),
            StructuredTool.from_function(
                coroutine=self.code_executor.execute_file,
                name="execute_file",
                description="执行指定的Python代码文件，路径相对于工作空间",
            ),
            StructuredTool.from_function(
                coroutine=self.code_executor.edit_code_file,
                name="edit_code_file",
                description="修改已存在的Python代码文件，写入完整的新代码内容",
            ),
            StructuredTool.from_function(
                coroutine=self.code_executor.list_code_files,
                name="list_code_files",
                description="列出当前工作空间下的所有Python代码文件",
            ),
        ]

    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        return (
            "你是一个专业的代码生成和执行助手。**务必确保成功产出所需文件再交付**，工作完成之前一定要调用工具，并根据执行结果迭代。\n\n"
            "工作流程：\n"
            "1. 分析用户任务，生成完整的Python代码\n"
            "2. 使用 save_and_execute 或 execute_code 运行代码，优先 save_and_execute 以便留存文件\n"
            "3. 仔细分析执行结果或错误信息\n"
            "4. 如需修改，使用 edit_code_file 或重新执行，直到成功\n"
            "5. 保存输出（图表、日志）到 outputs 或 logs 目录，文件名包含时间戳避免覆盖\n"
            "\n重复执行直到成功。"
        )

    async def run(self, task_prompt: str) -> str:
        """
        执行代码任务
        """
        logger.info("CodeAgent开始执行任务: %s", task_prompt[:100])
        if self.stream_manager:
            try:
                await self.stream_manager.send_json_block(
                    "code_agent_start", f"开始执行代码任务: {task_prompt[:100]}..."
                )
            except Exception as e:
                logger.warning("发送CodeAgent开始通知失败: %s", e)

        try:
            inputs = {"messages": [HumanMessage(content=task_prompt)]}
            result = await self.agent.ainvoke(inputs, config={"recursion_limit": 50})
            output = self._extract_output(result)

            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "code_agent_result",
                        output,
                    )
                except Exception as e:
                    logger.warning("发送CodeAgent完成通知失败: %s", e)

            return output
        except Exception as e:
            logger.error("CodeAgent执行失败: %s", e, exc_info=True)
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "code_agent_error", f"CodeAgent执行失败: {str(e)}"
                    )
                except Exception:
                    pass
            return f"CodeAgent执行失败: {str(e)}"

    def _extract_output(self, result: Any) -> str:
        """从AgentExecutor返回值中取出最终输出"""
        if isinstance(result, dict):
            if result.get("output"):
                return str(result["output"])
            messages = result.get("messages")
            if messages:
                for msg in reversed(messages):
                    content = getattr(msg, "content", None)
                    if not content and isinstance(msg, Dict):
                        content = msg.get("content")
                    if content:
                        return str(content)
        return str(result)

    def get_execution_summary(self) -> Dict[str, Any]:
        """返回执行概要"""
        return {
            "agent_type": "CodeAgent",
            "workspace_dir": self.workspace_dir,
            "work_id": self.work_id,
            "tools_count": len(self.tools),
            "tool_names": [tool.name for tool in self.tools],
            "langchain_based": True,
        }
