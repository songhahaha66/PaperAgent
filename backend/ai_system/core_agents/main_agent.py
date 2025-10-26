"""
主AI代理 - LangChain 重构版本
论文生成的中枢大脑，负责协调和规划整个论文生成过程
"""

import logging
import json
import os
import asyncio
import shutil
from typing import List, Dict, Any, Optional

from .agent_base import BaseAgent

logger = logging.getLogger(__name__)


class MainAgent(BaseAgent):
    """
    主LLM Agent (Orchestrator)，负责分析问题并委派任务
    基于 LangChain Agent，支持按步骤流式传输代理进度
    """

    def __init__(self, llm_handler: 'LLMHandler', stream_manager: 'StreamOutputManager',
                 work_id: Optional[str] = None, template_id: Optional[int] = None):
        """
        初始化MainAgent

        Args:
            llm_handler: LLM处理器
            stream_manager: 流式输出管理器
            work_id: 工作ID
            template_id: 模板ID
        """
        # MainAgent特有属性必须在基类初始化之前设置
        self.template_id = template_id

        # 构建工作空间目录路径
        workspace_dir = None
        if work_id:
            workspace_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                "pa_data", "workspaces", work_id
            )
            # 设置环境变量，供工具使用
            os.environ["WORKSPACE_DIR"] = workspace_dir

        # 初始化基类
        super().__init__(llm_handler, stream_manager, workspace_dir, work_id)

        # 如果有模板ID，复制模板文件到工作空间
        if self.template_id and work_id:
            self._copy_template_to_workspace()

        logger.info(f"MainAgent初始化完成，work_id: {work_id}, template_id: {template_id}")

    def get_system_prompt(self) -> str:
        """获取MainAgent的系统提示词 - 适配 LangChain Agent"""
        # 基础系统提示
        system_content = (
            "你是基于 LangChain Agent 的学术论文写作助手，负责协调整个论文生成过程。**你使用的语言需要跟模板语言一致**\n"
            "请你记住：论文尽可能使用图表等清晰表示！涉及图表等务必使用代码执行得到！\n"
            "请你记住：如果最后发现没找到代码或者图片就重新执行数据分析！\n"
            "你的职责：\n"
            "0. 请你生成论文为paper.md文档！！！\n"
            "1. 分析用户需求，制定论文生成计划\n"
            "2. **主动检查和分析附件**：当用户上传附件时，使用load_file工具读取附件内容\n"
            "3. 当需要代码执行、数据分析、图表生成时，使用execute_python_code工具\n"
            "4. 维护对话上下文，理解整个工作流程的连续性\n"
            "5. 使用save_file工具保存论文草稿\n\n"
            "**你的工具集**：\n"
            "- save_file: 保存论文草稿和内容到文件\n"
            "- load_file: 读取已保存的文件和附件内容\n"
            "- web_search: 搜索最新的学术资料和背景信息\n"
            "- execute_python_code: 执行数据分析、统计计算和图表生成\n\n"
            "重要原则：\n"
            "- 保持对话连贯性，按步骤执行任务\n"
            "- 你是中枢大脑，负责规划和协调，使用工具完成具体任务\n"
            "- **充分利用用户上传的附件内容，确保论文基于真实的资料和数据**\n"
            "- 所有生成的文件都要在最终论文中引用\n"
            "- 请自己执行迭代，直到任务完成\n"
            "- 生成的论文不要杜撰，确保科学性"
        )

        # 根据模板添加额外信息
        if self.template_id:
            system_content += f"\n\n**使用模板模式**：\n"
            system_content += f"- 模板文件为 'paper.md'（这是最终论文文件）\n"
            system_content += f"- 模板是一个大纲，你要填满大纲！\n"
            system_content += f"- 生成论文时必须严格遵循模板的格式、结构和风格\n"
            system_content += f"- 最终论文应该是一个完整的、格式规范的学术文档\n"
        else:
            system_content += f"\n\n**不使用模板模式**：\n"
            system_content += f"- 你需要从头开始创建完整的论文结构\n"
            system_content += f"- 根据用户需求设计合适的论文章节结构\n"
            system_content += f"- 确保论文结构完整、逻辑清晰\n"

        return system_content

    def _setup_tools(self):
        """MainAgent 使用 LangChain 工具管理器，不需要手动设置工具定义"""
        pass

    def _register_tool_functions(self):
        """MainAgent 使用 LangChain 工具，不需要手动注册工具函数"""
        pass

    def _copy_template_to_workspace(self):
        """复制模板文件到工作空间"""
        try:
            # 这里应该有复制模板的逻辑
            # 暂时简化实现
            logger.info(f"模板 {self.template_id} 复制到工作空间")
        except Exception as e:
            logger.error(f"复制模板失败: {e}")

    async def run(self, user_problem: str) -> str:
        """
        执行主Agent逻辑，使用 LangChain Agent 流式处理
        """
        logger.info(f"MainAgent开始执行任务: {user_problem}")

        try:
            # 添加用户消息到历史
            user_message = {"role": "user", "content": user_problem}
            self.messages.append(user_message)

            # 使用 LangChain LLM 处理器进行流式处理
            if self.tool_manager:
                tools = self.tool_manager.get_tools()
            else:
                tools = []

            assistant_message, tool_calls = await self.llm_handler.process_stream(
                self.messages, tools
            )

            # 处理工具调用（如果有的话）
            if tool_calls:
                logger.info(f"执行 {len(tool_calls)} 个工具调用")
                # 这里可以添加工具调用的处理逻辑

            # 添加助手回复到历史
            self.messages.append(assistant_message)

            # 返回最终结果
            result = assistant_message.get("content", "")
            logger.info(f"MainAgent任务完成，结果长度: {len(result)}")

            return result

        except Exception as e:
            logger.error(f"MainAgent执行失败: {e}", exc_info=True)
            error_msg = f"任务执行失败: {str(e)}"
            return error_msg

    async def _execute_code_task(self, task_prompt: str) -> str:
        """使用 SmolAgent 执行代码任务"""
        if not self.smolagent_manager:
            return "❌ SmolAgent 未初始化"

        try:
            result = await self.smolagent_manager.run(task_prompt)
            return result
        except Exception as e:
            logger.error(f"代码任务执行失败: {e}")
            return f"❌ 代码执行失败: {str(e)}"

    def get_execution_summary(self) -> Dict[str, Any]:
        """获取执行摘要"""
        return {
            "agent_type": "MainAgent",
            "template_id": self.template_id,
            "workspace_dir": self.workspace_dir,
            "tools_count": len(self.tools) if self.tools else 0,
            "langchain_based": True
        }