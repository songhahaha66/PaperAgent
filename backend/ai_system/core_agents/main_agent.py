"""
主AI代理 - LangChain 重构版本
论文生成的中枢大脑，负责协调和规划整个论文生成过程
"""

import logging
import os
import asyncio
from typing import List, Dict, Any, Optional
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import HumanMessage

from ..core_managers.langchain_tools import LangChainToolFactory

logger = logging.getLogger(__name__)


class MainAgent:
    """
    主LLM Agent (Orchestrator)，负责分析问题并委派任务
    基于 LangChain Agent，极简实现
    """

    def __init__(self, llm: BaseLanguageModel, stream_manager=None,
                 workspace_dir: str = None, work_id: Optional[str] = None,
                 template_id: Optional[int] = None, codeagent_llm=None):
        """
        初始化MainAgent

        Args:
            llm: LangChain LLM 实例
            stream_manager: 流式输出管理器
            workspace_dir: 工作空间目录路径
            work_id: 工作ID
            template_id: 模板ID
        """
        logger.info(f"MainAgent初始化开始，codeagent_llm: {codeagent_llm}")
        self.llm = llm
        self.stream_manager = stream_manager
        self.work_id = work_id
        self.template_id = template_id
        self.workspace_dir = workspace_dir

        # 如果没有提供workspace_dir但有work_id，构建路径
        if not workspace_dir and work_id:
            self.workspace_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                "pa_data", "workspaces", work_id
            )
            # 设置环境变量，供工具使用
            os.environ["WORKSPACE_DIR"] = self.workspace_dir

        # 创建所有需要的工具
        self.tools = LangChainToolFactory.create_all_tools(
            self.workspace_dir, stream_manager, include_template=True
        )

        # 添加SmolAgents工具（使用codeagent_llm）
        if codeagent_llm:
            smolagents_tool = LangChainToolFactory.create_smolagents_tool(
                self.workspace_dir, stream_manager, codeagent_llm
            )
            if smolagents_tool:
                self.tools.append(smolagents_tool)
                logger.info("成功添加SmolAgents工具，使用codeagent配置")
        else:
            logger.warning("未提供codeagent_llm，跳过SmolAgents工具")

        # 创建 LangChain Agent
        self.system_prompt = self._create_system_prompt()
        self.agent = create_agent(
            model=llm,
            tools=self.tools
        )

        logger.info(f"MainAgent初始化完成，work_id: {work_id}, template_id: {template_id}, 工具数量: {len(self.tools)}")

    def _create_system_prompt(self) -> str:
        """创建 MainAgent 的系统提示词"""
        # 基础系统提示
        system_content = (
            "你是基于 LangChain Agent 的学术论文写作助手，负责协调整个论文生成过程。**你使用的语言需要跟模板语言一致**\n\n"
            "请你记住：论文尽可能使用图表等清晰表示！涉及图表等务必使用代码执行得到！\n"
            "请你记住：如果最后发现没找到代码或者图片就重新执行数据分析！\n\n"
            "你的职责：\n"
            "1. 分析用户需求，制定论文生成计划\n"
            "2. **主动检查和分析附件**：当用户上传附件时，使用read_attachment工具读取附件内容\n"
            "3. 当需要代码执行、数据分析、图表生成时，使用smolagents_execute工具\n"
            "4. 使用writemd工具保存论文草稿到paper.md\n\n"
            "**你的工具集**：\n"
            "- writemd: 保存论文草稿和内容到文件（推荐使用）\n"
            "- update_template: 更新论文模板的特定章节\n"
            "- read_attachment: 读取附件内容（PDF、Word、Excel等）\n"
            "- list_attachments: 列出所有附件文件\n"
            "- web_search: 搜索最新的学术资料和背景信息\n"
            "- smolagents_execute: 使用SmolAgents执行复杂的代码任务，包括数据分析、图表生成、统计计算等（推荐用于复杂任务）\n"
            "- tree: 显示工作空间目录结构\n"
        )

        # 根据模板添加额外信息
        if self.template_id:
            system_content += (
                f"\n\n**使用模板模式**（template_id: {self.template_id}）：\n"
                f"- 模板文件为 'paper.md'（这是最终论文文件）\n"
                f"- 模板是一个大纲，你要填满大纲！\n"
                f"- 生成论文时必须严格遵循模板的格式、结构和风格\n"
                f"- 优先使用update_template工具来更新特定章节\n"
                f"- 最终论文应该是一个完整的、格式规范的学术文档\n"
            )
        else:
            system_content += (
                f"\n\n**不使用模板模式**：\n"
                f"- 你需要从头开始创建完整的论文结构\n"
                f"- 根据用户需求设计合适的论文章节结构\n"
                f"- 使用writemd工具创建paper.md文件\n"
                f"- 确保论文结构完整、逻辑清晰\n"
            )

        system_content += (
            "\n\n重要原则：\n"
            "- 保持对话连贯性，按步骤执行任务\n"
            "- **充分利用用户上传的附件内容，确保论文基于真实的资料和数据**\n"
            "- 生成的图表要保存在outputs目录，并在论文中正确引用\n"
            "- 论文不要杜撰，确保科学性和准确性\n"
            "- 每完成一个重要章节，使用writemd保存一次\n"
            "- 最终输出应该是完整的paper.md文件\n"
        )

        return system_content

    async def run(self, user_input: str) -> str:
        """
        执行主Agent逻辑，使用 LangChain Agent 处理
        """
        logger.info(f"MainAgent开始执行任务: {user_input[:100]}...")

        try:
            # 发送开始通知
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "main_agent_start",
                        f"MainAgent开始执行: {user_input[:100]}..."
                    )
                except Exception as e:
                    logger.warning(f"发送开始通知失败: {e}")

            # 使用 LangChain Agent 执行
            inputs = {"messages": [HumanMessage(content=user_input)]}
            result = await self.agent.ainvoke(inputs)

            # 提取最后的AI回复
            messages = result.get("messages", [])
            output = ""
            for message in reversed(messages):
                if hasattr(message, 'content') and message.content:
                    output = message.content
                    break
                elif isinstance(message, dict) and message.get("role") == "assistant":
                    output = message.get("content", "")
                    break

            logger.info(f"MainAgent任务完成，结果长度: {len(output)}")

            # 发送完成通知
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "main_agent_complete",
                        f"任务完成，结果长度: {len(output)} 字符"
                    )
                except Exception as e:
                    logger.warning(f"发送完成通知失败: {e}")

            return output

        except Exception as e:
            logger.error(f"MainAgent执行失败: {e}", exc_info=True)
            error_msg = f"任务执行失败: {str(e)}"

            # 发送错误通知
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block("main_agent_error", error_msg)
                except Exception as e:
                    logger.warning(f"发送错误通知失败: {e}")

            return error_msg

    async def stream_run(self, user_input: str):
        """
        流式执行，逐个输出Agent步骤
        """
        logger.info(f"MainAgent开始流式执行: {user_input[:100]}...")

        try:
            # 使用异步流式执行
            inputs = {"messages": [HumanMessage(content=user_input)]}
            async for chunk in self.agent.astream(inputs, stream_mode="updates"):
                if self.stream_manager:
                    try:
                        await self.stream_manager.print_stream(str(chunk))
                    except Exception as e:
                        logger.warning(f"流式输出失败: {e}")
                else:
                    print(str(chunk))

        except Exception as e:
            logger.error(f"流式执行失败: {e}")
            error_msg = f"流式执行失败: {str(e)}"
            if self.stream_manager:
                await self.stream_manager.print_content(error_msg)

    def get_execution_summary(self) -> Dict[str, Any]:
        """获取执行摘要"""
        return {
            "agent_type": "MainAgent",
            "template_id": self.template_id,
            "work_id": self.work_id,
            "workspace_dir": self.workspace_dir,
            "tools_count": len(self.tools),
            "tool_names": [tool.name for tool in self.tools],
            "langchain_based": True
        }