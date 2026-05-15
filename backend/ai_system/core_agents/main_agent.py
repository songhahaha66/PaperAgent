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
from .review_agent import ReviewAgent
from config.paths import get_workspace_path
from services.file_services.template_contract import read_template_contract

logger = logging.getLogger(__name__)


class MainAgent:
    """
    主LLM Agent (Orchestrator)，负责分析问题并委派任务
    基于 LangChain Agent，极简实现
    """

    def __init__(self, llm: BaseLanguageModel, stream_manager=None,
                 workspace_dir: str = None, work_id: Optional[str] = None,
                 template_id: Optional[int] = None, codeagent_llm=None,
                 output_mode: str = "markdown", writer_llm=None):
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
            writer_llm: WriterAgent使用的LLM实例（从"writing"配置加载）
        """
        logger.info(f"MainAgent初始化开始，output_mode: {output_mode}, codeagent_llm: {codeagent_llm}, writer_llm: {writer_llm}")
        self.llm = llm
        self.stream_manager = stream_manager
        self.work_id = work_id
        self.template_id = template_id
        self.workspace_dir = workspace_dir
        self.output_mode = output_mode

        # 如果没有提供workspace_dir但有work_id，构建路径
        if not workspace_dir and work_id:
            # 使用统一的路径配置
            self.workspace_dir = str(get_workspace_path(work_id))
            # 设置环境变量，供工具使用
            os.environ["WORKSPACE_DIR"] = self.workspace_dir

        if self.output_mode == "markdown":
            self.tools = LangChainToolFactory.create_file_tools(
                self.workspace_dir, stream_manager
            )
            base_extras = LangChainToolFactory.create_base_tools(
                self.workspace_dir, stream_manager
            )
            existing_names = {t.name for t in self.tools}
            for t in base_extras:
                if t.name not in existing_names:
                    self.tools.append(t)
            logger.info("Markdown模式：MainAgent直接使用文件工具，不使用WriterAgent")
        else:
            self.tools = LangChainToolFactory.create_base_tools(
                self.workspace_dir, stream_manager
            )
            writer_agent_tool = LangChainToolFactory.create_writer_agent_tool(
                workspace_dir=self.workspace_dir,
                output_mode=self.output_mode,
                stream_manager=stream_manager,
                llm=self.llm,
                writer_llm=writer_llm
            )
            if writer_agent_tool:
                self.tools.append(writer_agent_tool)
                logger.info("成功添加WriterAgent工具，output_mode: %s", self.output_mode)
            else:
                logger.warning("WriterAgent工具创建失败，文档写作能力可能受限")

        code_llm = codeagent_llm or self.llm
        code_agent_tool = LangChainToolFactory.create_code_agent_tool(
            self.workspace_dir, stream_manager, code_llm
        )
        if code_agent_tool:
            self.tools.append(code_agent_tool)
            logger.info("成功添加CodeAgent工具，使用langchain实现")
        else:
            logger.warning("CodeAgent工具创建失败，代码能力可能受限")

        # 创建 LangChain Agent
        self.system_prompt = self._create_system_prompt()
        
        # 检查 LLM 是否支持工具调用
        logger.info(f"LLM 类型: {type(llm).__name__}")
        logger.info(f"LLM 模型: {getattr(llm, 'model_name', getattr(llm, 'model', 'unknown'))}")
        
        # 检查是否有 bind_tools 方法（表示支持工具调用）
        if hasattr(llm, 'bind_tools'):
            logger.info("✓ LLM 支持 bind_tools 方法")
            # 测试绑定工具
            try:
                test_bound = llm.bind_tools(self.tools[:1])
                logger.info("✓ 工具绑定测试成功")
            except Exception as e:
                logger.error(f"✗ 工具绑定测试失败: {e}")
        else:
            logger.warning("⚠️ LLM 不支持 bind_tools 方法，工具调用可能不可用")
        
        # 检查 LLM 的配置
        if hasattr(llm, 'model_kwargs'):
            logger.info(f"LLM model_kwargs: {llm.model_kwargs}")
        
        logger.info(f"创建 Agent，工具数量: {len(self.tools)}")
        self.agent = create_agent(
            model=llm,
            tools=self.tools,
            system_prompt=self.system_prompt,
            debug=True  # 启用调试模式
        )

        self.review_agent = ReviewAgent(
            llm=llm, workspace_dir=self.workspace_dir, output_mode=self.output_mode
        )

        logger.info(f"MainAgent初始化完成，work_id: {work_id}, template_id: {template_id}, output_mode: {output_mode}, 工具数量: {len(self.tools)}")
        logger.info(f"已注册工具: {[tool.name for tool in self.tools]}")

    def _create_system_prompt(self) -> str:
        """创建 MainAgent 的系统提示词"""
        # 基础系统提示
        system_content = (
            "你是基于 LangChain Agent 的学术论文写作助手（MainAgent），负责协调整个论文生成过程。**你使用的语言需要跟模板语言一致**\n\n"
            "**🔴 核心行为准则**：\n"
            "1. **主动执行，不要问用户要写什么内容！**\n"
            "2. **根据用户的需求描述，自己思考并生成完整的论文内容**\n"
            "3. **立即使用工具开始写作，不要只是回复文本说明！**\n"
            "4. **如果用户说\"写论文\"、\"生成论文\"，你要立即开始调用工具写入内容，而不是问用户要写什么**\n\n"
            "**重要：你必须使用提供的工具来完成任务，不要只是回复文本！**\n\n"
            "请你记住：论文尽可能使用图表等清晰表示！涉及图表等务必使用代码执行得到！\n"
            "请你记住：如果最后发现没找到代码或者图片就重新执行数据分析！\n\n"
            "**你的身份和职责**：\n"
            "- 你是MainAgent，负责论文写作的整体协调和文档生成\n"
            "- 你有一个助手CodeAgent，专门负责编程任务（数据分析、图表生成等）\n"
            "- 你有一个助手WriterAgent，专门负责文档写作（章节创作、内容写入）\n"
            "- 你需要明确区分哪些任务由谁完成\n"
            "- **你要主动思考论文内容，不要总是问用户要写什么**\n\n"
            "**🎯 Plan-Driven 全流程规划（核心工作模式）**：\n"
            "你必须遵循以下全流程规划模式，类似spec coding的plan-driven方式：\n\n"
            "**Phase 1: 状态感知**\n"
            "- 调用 get_paper_status 了解paper当前状态（已有章节、写作进度、内容摘要）\n"
            "- 理解用户需求和论文目标\n\n"
            "**Phase 2: 制定写作计划并保存**\n"
            "- 根据用户需求和当前状态，列出完整的写作计划\n"
            "- **必须调用 update_plan 工具保存计划**（工具会同步生成 plan.md 与结构化 plan.json，用户前端固定展示 plan.json）\n"
            "- 计划使用Markdown表格作为输入兼容格式，包含序号、章节名、状态、说明；系统会转换为动态结构化计划\n"
            "- 计划必须是动态的：标出当前进行项、完成项、待做项；执行过程中根据真实进展更新状态，不要把计划当一次性静态清单\n"
            "- 已完成的章节标记为「✅ 已完成」，不要重复写作\n"
            "- 待写章节标记为「⬜ 待写」\n"
            "- 需要数据分析/图表的章节，先规划CodeAgent任务\n\n"
            "**Phase 3: 逐步执行并更新计划**\n"
            "- 按计划逐章节执行，每次只写一个章节\n"
            "- **不要每个章节前后都调 update_plan，只在关键节点更新动态计划**：\n"
            "  * 开始写作前更新一次（标记第一个章节为⏳）\n"
            "  * 每完成 2-3 个章节后批量更新一次状态\n"
            "  * 全部完成后最终更新一次\n"
            "- 需要图表的章节，先让CodeAgent生成数据/图表，再写入\n"
            "- 每完成一个章节，调用 get_paper_status 确认写入成功\n\n"
            "**Phase 4: 验收检查**\n"
            "- 所有章节完成后，调用 get_paper_status 做最终确认\n"
            "- 调用 update_plan 更新最终计划状态，确保所有已完成任务标记为 ✅\n"
            "- 向用户报告完成情况\n\n"
        )

        # 根据输出模式添加文档生成指令
        if self.output_mode == "word":
            system_content += (
                "4. **委派文档写作任务给WriterAgent**：你正在Word模式下工作，必须使用WriterAgent来处理所有文档操作\n\n"
                "**🔴 核心原则：高层指令，WriterAgent自主创作**\n"
                "- **你（MainAgent）只需要给WriterAgent高层次的写作目标，不要指定具体内容**\n"
                "- **WriterAgent是专业的写作助手，会根据你的目标自主扩充和创作内容**\n"
                "- 使用 writer_agent_execute 工具来委派文档操作\n\n"
                "**WriterAgent工具使用方法**：\n"
                "- 工具名称：writer_agent_execute\n"
                "- 输入：高层次的写作目标（不是具体内容）\n"
                "- WriterAgent会理解目标，自主创作内容，并选择合适的Word工具完成\n\n"
                "**✅ 正确的指令示例（高层次目标）**：\n"
                "- \"写一个Introduction章节，介绍圆周率的重要性和研究意义\"\n"
                "- \"写一个History章节，讲述圆周率的历史发展\"\n"
                "- \"写一个Applications章节，说明圆周率在各领域的应用\"\n"
                "- \"插入图片outputs/chart.png并配上说明文字\"\n"
                "- \"创建一个表格展示实验结果数据\"\n\n"
                "**❌ 错误的指令示例（过于具体）**：\n"
                "- \"添加一级标题Introduction\" ← 太具体，WriterAgent无法发挥\n"
                "- \"添加段落内容：圆周率π是...\" ← 不要写具体内容，让WriterAgent自己写\n"
                "- \"添加3行4列的表格\" ← 不要指定格式细节\n\n"
                "**Word模式工作流程（立即执行，不要问用户）**：\n"
                "1. 分析用户需求，确定论文需要哪些章节和内容主题\n"
                "2. **给WriterAgent下达高层次的写作目标**（例如：\"写Introduction章节\"）\n"
                "3. WriterAgent会自主决定：\n"
                "   - 章节标题的具体文字\n"
                "   - 段落的具体内容和表述\n"
                "   - 内容的组织结构和逻辑\n"
                "   - 使用哪些Word工具（标题、段落、表格等）\n"
                "4. 如果需要图表，先使用code_agent_execute生成图片，然后让WriterAgent插入并配文字\n"
                "5. 文档会自动保存到 paper.docx\n\n"
                "**任务分工原则（重要）**：\n"
                "- **你（MainAgent）负责**：战略规划、章节划分、主题确定\n"
                "  * 决定论文需要哪些章节（Introduction, Methods, Results等）\n"
                "  * 给WriterAgent下达章节级别的写作目标\n"
                "  * 协调CodeAgent生成数据和图表\n"
                "  * 不要写具体内容，不要指定格式细节\n"
                "- **WriterAgent负责**：内容创作、文档操作、格式控制\n"
                "  * 根据MainAgent的目标自主创作具体内容\n"
                "  * 决定标题文字、段落内容、表格结构等细节\n"
                "  * 选择合适的Word工具完成文档操作\n"
                "- **CodeAgent负责**：数据分析、图表生成、复杂计算\n"
                "  * 使用 code_agent_execute 工具委派编程任务\n"
                "  * 例如：\"分析数据并生成柱状图\"、\"计算统计指标\"\n\n"
            )
        else:
            system_content += (
                "4. **Markdown模式 — 你直接操作文件工具撰写论文**\n\n"
                "**🔴 核心原则：你亲自写作，不委派**\n"
                "- 你直接调用 writemd / readmd / get_paper_status 等工具完成文档操作\n"
                "- 不存在 WriterAgent，你自己就是写作者\n"
                "- 每次只写一个章节，用 section_update 或 append 模式\n\n"
                "**🔴 防止内容丢失的关键规则**：\n"
                "- 每次写作前必须先调用 get_paper_status 查看当前状态\n"
                "- **绝对不要在 writemd 的 content 里包含多个章节的骨架**，每次只传一个章节\n"
                "- 更新已有章节 → mode='section_update'，content 以该章节的 ## 标题开头\n"
                "- 添加新章节 → mode='append'\n"
                "- **禁止使用 overwrite 模式**\n"
                "- **禁止对 # 一级标题做 section_update**（一级标题下面是整篇文档，会导致全文被替换）\n\n"
                "**Markdown模式工作流程（立即执行，不要问用户）**：\n"
                "1. **首先调用 get_paper_status** 查看论文当前结构和各章节写作进度\n"
                "2. 如果 paper.md 不存在或为空，用 writemd(mode='overwrite') 写入初始骨架（仅限首次）\n"
                "   - **骨架的一级标题必须是论文的真实标题**，不要写「# 论文标题」这种占位符\n"
                "   - 从用户需求中提取论文主题，生成恰当的标题\n"
                "   - 骨架只包含标题层级，不包含正文内容，例如：\n"
                "     ```\n"
                "     # 基于深度学习的图像分类研究\n"
                "     ## 摘要\n"
                "     ## 1. 引言\n"
                "     ## 2. 相关工作\n"
                "     ...\n"
                "     ```\n"
                "3. 逐章节写作：\n"
                "   a. 调用 readmd 读取当前文档内容\n"
                "   b. 思考该章节要写什么具体内容\n"
                "   c. 调用 writemd(mode='section_update', content='## 章节名\\n\\n具体内容...')\n"
                "   d. 调用 get_paper_status 确认写入成功\n"
                "4. 重复步骤 3 直到所有章节完成\n\n"
                "**✅ 正确的 writemd 调用示例**：\n"
                "```\n"
                "writemd(filename='paper', mode='section_update', content='## 摘要\\n\\n本研究旨在...')\n"
                "writemd(filename='paper', mode='section_update', content='## 引言\\n\\n研究背景...')\n"
                "writemd(filename='paper', mode='append', content='## 参考文献\\n\\n[1] ...')\n"
                "```\n\n"
                "**❌ 错误的 writemd 调用**：\n"
                "- content 里包含整篇文档骨架（会导致内容重复）\n"
                "- 对 # 一级标题做 section_update（会替换整篇文档）\n"
                "- 使用 overwrite 模式覆盖已有内容\n\n"
                "**任务分工**：\n"
                "- **你（MainAgent）负责**：规划 + 写作 + 文件操作\n"
                "- **CodeAgent负责**：数据分析、图表生成\n"
                "  * 使用 code_agent_execute 工具委派编程任务\n"
            )

        system_content += (
            "\n**通用工具**：\n"
            "- get_paper_status: 🔴必用！获取paper.md的写作状态（章节结构、进度、内容摘要），写作前必须先调用\n"
            "- update_plan: 🔴必用！保存/更新动态写作计划，系统会写入 plan.md 和 plan.json，用户前端以固定结构查看进度\n"
            "- list_attachments: 列出所有附件文件\n"
            "- web_search: 搜索最新的学术资料和背景信息\n"
            "- tree: 显示工作空间目录结构\n\n"
        )

        if self.output_mode == "markdown":
            system_content += (
                "**Markdown写作工具（你直接调用）**：\n"
                "- readmd: 读取Markdown文件内容，写入前必须先读取\n"
                "- writemd: 写入Markdown内容（mode: section_update/append/insert）\n"
                "- update_template: 更新论文模板中的特定章节\n\n"
            )
        else:
            system_content += (
                "**WriterAgent工具（用于所有文档写作任务）**：\n"
                "- writer_agent_execute: 委派给专用WriterAgent执行文档写作任务\n"
                "  * ✅ 适用场景：创建文档、添加标题、添加段落、添加表格、插入图片、格式化文档等所有文档操作\n"
                "  * **所有文档写作任务必须通过WriterAgent完成**\n"
                "  * WriterAgent会根据output_mode自动使用Word工具\n\n"
            )

        system_content += (
            "**CodeAgent工具（仅用于编程任务）**：\n"
            "- code_agent_execute: 委派给专用CodeAgent执行编程任务\n"
            "  * ✅ 适用场景：数据分析、图表生成（matplotlib/seaborn）、统计计算、需要执行Python代码的复杂计算\n"
            "  * ❌ 禁止场景：**绝对不要使用CodeAgent来创建、编辑、修改文档**\n"
            "  * ❌ 禁止场景：**不要用CodeAgent读取文本文件！** 读文件请直接用 readmd / list_attachments / tree\n"
            "  * ⚠️ CodeAgent每次调用会消耗大量执行步数，请谨慎使用，仅在真正需要执行代码时才调用\n\n"
            "**📂 文件系统规则**：\n"
            "- 所有路径使用 workspace 相对路径（如 paper.md、code/main.py、outputs/figure.png），不要使用绝对路径\n"
            "- 代码中**不要手动 savefig/to_csv 到 outputs/**，图表和数据文件会自动保存到 runs/ 执行记录中\n"
            "- 如需将图表作为论文正式引用，使用 promote_artifact(run_id, artifact_name, output_name) 将产物从 runs/ 晋升到 outputs/\n"
            "- **不要通过代码（shutil.copy等）手动复制文件到 outputs/，必须使用 promote_artifact 工具**\n"
            "- 正式源码通过 save_and_execute 保存到 code/；普通 execute_code 只产生执行记录\n"
            "- runs/ 和 .system/ 是系统内部目录，不要读取或引用其中的文件路径\n\n"
            "**🚫 严格禁止事项**：\n"
            "- **永远不要让CodeAgent操作文档（Word或Markdown）！**\n"
            "- **永远不要让CodeAgent使用python-docx库或直接写入.md文件！**\n"
            "- **不要用CodeAgent读取.txt/.csv/.md等文本文件，用readmd或list_attachments代替！**\n"
            "- CodeAgent只负责生成数据、图表等内容，不负责将内容写入文档\n"
        )

        # 根据模板添加额外信息
        if self.template_id:
            system_content += (
                f"\n\n**使用模板模式**（template_id: {self.template_id}）：\n"
                f"- 模板骨架已经初始化到最终论文文件：Markdown 模式为 paper.md，Word 模式为 paper.docx\n"
                f"- 模板是强制骨架，不是参考资料；你必须填满骨架，不得删除或重排模板章节/表格/占位栏位\n"
                f"- 生成论文时必须严格遵循模板的格式、结构和风格\n"
                f"- 模板中出现的格式要求（例如“宋体”“小三”“居中”“行距”等）必须遵循\n"
                f"- Markdown 模式优先使用 update_template 或 section_update 更新特定章节\n"
                f"- Word 模式必须让 WriterAgent 先读取现有 paper.docx，并基于该模板底稿生成，不能另起一套结构\n"
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
            "\n\n**🎯 重要原则**：\n"
            "- **主动执行，不要问用户要写什么！根据需求自己思考并生成内容！**\n"
            "- **立即使用工具开始写作，不要只是说明你会怎么做！**\n"
            "- 保持对话连贯性，按步骤执行任务\n"
            "- 生成的图表要保存在outputs目录，并在论文中正确引用\n"
            "- 论文不要杜撰，确保科学性和准确性\n"
            "- 每完成一个重要章节，确认写入成功后再写下一个\n"
            "- 最终输出应该是完整的paper.md或paper.docx文件\n"
            "\n**🔴 关键要求：任务完成标准**\n"
            "- **你的任务只有在将最终结果输出到文件后才算真正完成！**\n"
            "- Word模式：必须使用Word工具将所有内容写入paper.docx文件\n"
            "- Markdown模式：必须逐章节通过 writemd 工具写入paper.md文件（使用 section_update 模式），绝不能一次性overwrite覆盖\n"
            "- **不要只是在对话中回复内容，必须调用相应的工具将内容保存到文件中**\n"
            "- 在完成文件输出后，向用户确认文件已生成并说明文件路径\n"
            "- 如果没有将内容写入docx或md文件，任务视为未完成\n"
        )

        template_contract = read_template_contract(self.workspace_dir) if self.workspace_dir else ""
        if template_contract:
            system_content += (
                "\n\n**📌 当前工作区模板契约（最高优先级写作约束）**\n"
                "以下内容来自用户上传的模板骨架和格式要求，必须逐条遵循：\n\n"
                f"{template_contract}\n"
            )

        return system_content

    MAX_CONTINUATIONS = 3

    @staticmethod
    def _count_tool_calls(messages: list) -> int:
        return sum(
            len(msg.tool_calls)
            for msg in messages
            if hasattr(msg, 'tool_calls') and msg.tool_calls
        )

    @staticmethod
    def _extract_output(messages: list) -> str:
        for message in reversed(messages):
            if hasattr(message, 'content') and message.content:
                return message.content
            if isinstance(message, dict) and message.get("role") == "assistant":
                return message.get("content", "")
        return ""

    async def run(self, user_input: str) -> str:
        logger.info(f"MainAgent开始执行任务: {user_input[:100]}...")

        try:
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "main_agent_start",
                        f"MainAgent开始执行: {user_input[:100]}..."
                    )
                except Exception as e:
                    logger.warning(f"发送开始通知失败: {e}")

            logger.info(f"调用 Agent，可用工具数量: {len(self.tools)}")
            logger.info(f"工具列表: {[tool.name for tool in self.tools]}")

            all_messages: list = [HumanMessage(content=user_input)]
            result_messages: list = []

            for attempt in range(self.MAX_CONTINUATIONS + 1):
                result = await self.agent.ainvoke(
                    {"messages": all_messages},
                    config={"recursion_limit": 150},
                )
                result_messages = result.get("messages", [])

                tool_calls_count = self._count_tool_calls(result_messages)
                logger.info(
                    f"[attempt {attempt}] Agent 返回 {len(result_messages)} 条消息, "
                    f"{tool_calls_count} 次工具调用"
                )

                review = await self.review_agent.review(user_input)
                logger.info(
                    f"[attempt {attempt}] ReviewAgent 判定: complete={review.complete}, "
                    f"reason={review.reason}"
                )

                if review.complete:
                    logger.info(f"✓ ReviewAgent 确认任务完成 (attempt {attempt})")
                    break

                if attempt < self.MAX_CONTINUATIONS:
                    logger.warning(
                        f"⚠️ ReviewAgent 判定未完成 (attempt {attempt}/{self.MAX_CONTINUATIONS})"
                    )
                    if self.stream_manager:
                        try:
                            await self.stream_manager.send_json_block(
                                "continuation",
                                f"ReviewAgent 检测到论文未完成，自动续写 "
                                f"({attempt + 1}/{self.MAX_CONTINUATIONS}): {review.reason}"
                            )
                        except Exception:
                            pass
                    all_messages = result_messages + [
                        HumanMessage(content=review.continuation_prompt)
                    ]
                else:
                    logger.warning("⚠️ 达到最大续写次数，停止续写")

            output = self._extract_output(result_messages)
            logger.info(f"MainAgent任务完成，结果长度: {len(output)}")

            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "main_agent_complete",
                        output
                    )
                except Exception as e:
                    logger.warning(f"发送完成通知失败: {e}")

            return output

        except Exception as e:
            logger.error(f"MainAgent执行失败: {e}", exc_info=True)
            error_msg = f"任务执行失败: {str(e)}"

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
