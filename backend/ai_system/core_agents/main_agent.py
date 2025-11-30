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
from config.paths import get_workspace_path

logger = logging.getLogger(__name__)


class MainAgent:
    """
    主LLM Agent (Orchestrator)，负责分析问题并委派任务
    基于 LangChain Agent，极简实现
    """

    def __init__(self, llm: BaseLanguageModel, stream_manager=None,
                 workspace_dir: str = None, work_id: Optional[str] = None,
                 template_id: Optional[int] = None, codeagent_llm=None,
                 output_mode: str = "markdown", mcp_manager=None):
        """
        初始化MainAgent

        Args:
            llm: LangChain LLM 实例
            stream_manager: 流式输出管理器
            workspace_dir: 工作空间目录路径
            work_id: 工作ID
            template_id: 模板ID
            codeagent_llm: CodeAgent使用的LLM实例
            output_mode: 输出模式 ("markdown", "word", "latex")
            mcp_manager: MCP客户端管理器，用于Word工具
        """
        logger.info(f"MainAgent初始化开始，output_mode: {output_mode}, codeagent_llm: {codeagent_llm}")
        self.llm = llm
        self.stream_manager = stream_manager
        self.work_id = work_id
        self.template_id = template_id
        self.workspace_dir = workspace_dir
        self.output_mode = output_mode
        self.mcp_manager = mcp_manager

        # 如果没有提供workspace_dir但有work_id，构建路径
        if not workspace_dir and work_id:
            # 使用统一的路径配置
            self.workspace_dir = str(get_workspace_path(work_id))
            # 设置环境变量，供工具使用
            os.environ["WORKSPACE_DIR"] = self.workspace_dir

        # 创建所有需要的工具
        self.tools = LangChainToolFactory.create_all_tools(
            self.workspace_dir, stream_manager, include_template=True
        )

        # 添加代码执行工具（使用CodeAgent，默认复用主LLM，可指定codeagent_llm）
        code_llm = codeagent_llm or self.llm
        code_agent_tool = LangChainToolFactory.create_code_agent_tool(
            self.workspace_dir, stream_manager, code_llm
        )
        if code_agent_tool:
            self.tools.append(code_agent_tool)
            logger.info("成功添加CodeAgent工具，使用langchain实现")
        else:
            logger.warning("CodeAgent工具创建失败，代码能力可能受限")

        # 标记是否需要加载Word工具（延迟到第一次run时加载）
        self.word_tool_wrapper = None
        self._word_tools_loaded = False

        # 创建 LangChain Agent
        self.system_prompt = self._create_system_prompt()
        self.agent = create_agent(
            model=llm,
            tools=self.tools
        )

        logger.info(f"MainAgent初始化完成，work_id: {work_id}, template_id: {template_id}, output_mode: {output_mode}, 工具数量: {len(self.tools)}")

    async def _load_word_tools_async(self) -> None:
        """异步加载Word工具，包含回退逻辑"""
        try:
            # 检查LaTeX模式（尚未实现）
            if self.output_mode == "latex":
                logger.warning("LaTeX模式尚未实现，回退到Markdown模式")
                self.output_mode = "markdown"
                if self.stream_manager:
                    await self.stream_manager.send_json_block(
                        "warning",
                        "LaTeX模式即将推出，当前已回退到Markdown模式"
                    )
                return

            # 检查Word模式的MCP可用性
            if not self.mcp_manager or not self.mcp_manager.is_available():
                logger.warning("Word模式请求但MCP服务器不可用，将回退到Markdown模式")
                self.output_mode = "markdown"
                if self.stream_manager:
                    await self.stream_manager.send_json_block(
                        "error",
                        "Word模式不可用：MCP服务器未连接。已回退到Markdown模式。"
                    )
                return

            # 导入WordToolWrapper
            from ..core_managers.word_tools import WordToolWrapper

            # 创建WordToolWrapper
            mcp_client = self.mcp_manager.get_client()
            self.word_tool_wrapper = WordToolWrapper(
                mcp_client=mcp_client,
                workspace_dir=self.workspace_dir,
                stream_manager=self.stream_manager
            )

            # 初始化Word工具
            success = await self.word_tool_wrapper.initialize()
            if not success:
                logger.warning("Word工具初始化失败，回退到Markdown模式")
                self.output_mode = "markdown"
                self.word_tool_wrapper = None
                if self.stream_manager:
                    await self.stream_manager.send_json_block(
                        "warning",
                        "Word工具初始化失败，已回退到Markdown模式"
                    )
                return

            # 获取LangChain兼容的工具
            word_tools = self.word_tool_wrapper.create_langchain_tools()
            self.tools.extend(word_tools)

            logger.info(f"成功加载 {len(word_tools)} 个Word工具到MainAgent")
            if self.stream_manager:
                await self.stream_manager.send_json_block(
                    "info",
                    f"Word模式已启用，加载了 {len(word_tools)} 个Word工具"
                )

        except Exception as e:
            logger.error(f"加载Word工具失败: {e}", exc_info=True)
            self.output_mode = "markdown"
            self.word_tool_wrapper = None
            if self.stream_manager:
                await self.stream_manager.send_json_block(
                    "error",
                    f"Word工具加载失败: {str(e)}，已回退到Markdown模式"
                )

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
            "3. 当需要代码执行、数据分析、图表生成时，使用code_agent_execute工具\n"
        )

        # 根据输出模式添加文档生成指令
        if self.output_mode == "word":
            system_content += (
                "4. **使用Word工具生成论文文档**：你正在Word模式下工作，必须使用Word工具创建.docx格式的论文\n\n"
                "**Word模式工具集**：\n"
                "- word_create_document: 创建新的Word文档（必须首先调用）\n"
                "- word_add_heading: 添加标题（level 1-5，1为最大标题）\n"
                "  * 示例：word_add_heading(text=\"Introduction\", level=1)\n"
                "- word_add_paragraph: 添加段落文本\n"
                "  * 示例：word_add_paragraph(text=\"This paper presents...\")\n"
                "- word_add_table: 添加表格\n"
                "  * 示例：word_add_table(rows=3, cols=4, data=[[\"Header1\", \"Header2\", ...], ...])\n"
                "- word_add_picture: 插入图片（路径相对于工作空间）\n"
                "  * 示例：word_add_picture(image_path=\"outputs/chart.png\", width=6.0)\n"
                "- word_save_document: 保存文档到paper.docx（完成后必须调用）\n\n"
                "**Word模式工作流程**：\n"
                "1. 首先调用 word_create_document 创建文档\n"
                "2. 使用 word_add_heading 添加章节标题\n"
                "3. 使用 word_add_paragraph 添加段落内容\n"
                "4. 使用 word_add_table 添加数据表格\n"
                "5. 使用 word_add_picture 插入图表（图片需先通过code_agent_execute生成）\n"
                "6. 最后调用 word_save_document 保存文档\n\n"
                "**重要提示**：\n"
                "- 图片路径使用相对路径（如 \"outputs/chart.png\"）\n"
                "- 标题层级：1=章节标题，2=小节标题，3=子小节标题\n"
                "- 每次添加内容后无需保存，只在最后调用一次 word_save_document\n"
                "- 如果图片文件不存在，系统会跳过该图片并继续\n\n"
            )
        else:
            system_content += (
                "4. 使用writemd工具保存论文草稿到paper.md\n\n"
                "**Markdown模式工具集**：\n"
                "- writemd: 保存论文草稿和内容到文件（推荐使用）\n"
                "- update_template: 更新论文模板的特定章节\n"
            )

        # 通用工具
        system_content += (
            "\n**通用工具**：\n"
            "- read_attachment: 读取附件内容（PDF、Word、Excel等）\n"
            "- list_attachments: 列出所有附件文件\n"
            "- web_search: 搜索最新的学术资料和背景信息\n"
            "- code_agent_execute: 使用专用CodeAgent执行复杂的代码任务，包括数据分析、图表生成、统计计算等（推荐用于复杂任务）\n"
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
            # 首次运行时处理输出模式和加载工具
            if not self._word_tools_loaded:
                # 处理LaTeX模式回退
                if self.output_mode == "latex":
                    logger.warning("LaTeX模式尚未实现，回退到Markdown模式")
                    self.output_mode = "markdown"
                    if self.stream_manager:
                        await self.stream_manager.send_json_block(
                            "warning",
                            "LaTeX模式即将推出，当前已回退到Markdown模式"
                        )
                
                # 加载Word工具（如果需要）
                elif self.output_mode == "word":
                    await self._load_word_tools_async()
                
                self._word_tools_loaded = True

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
