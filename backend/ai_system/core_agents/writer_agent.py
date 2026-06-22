"""
WriterAgent - Specialized agent for document writing operations
Handles Word and Markdown document generation by directly calling appropriate tools
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, List

from langchain.agents import create_agent
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool

from ..core_managers.langchain_tools import LangChainToolFactory
from services.file_services.template_contract import read_template_contract

logger = logging.getLogger(__name__)


class WriterAgent:
    """
    Specialized LangChain-based agent for document writing operations.
    Separates document writing concerns from MainAgent orchestration.
    
    Supports multiple output modes:
    - word: Uses Word tools for .docx document generation
    - markdown: Uses Markdown tools for .md document generation
    - latex: Not yet supported (logs warning)
    """

    def __init__(
        self,
        llm: BaseLanguageModel,
        output_mode: str,
        workspace_dir: str,
        stream_manager=None,
        work_id: Optional[str] = None,
    ):
        """
        Initialize WriterAgent with specified output mode and tools.
        
        Args:
            llm: LangChain LLM instance for agent execution
            output_mode: Document format ("word", "markdown", or "latex")
            workspace_dir: Workspace directory path for file operations
            stream_manager: Stream manager for output notifications
            work_id: Work ID for tracking and logging
            
        Raises:
            ValueError: If workspace_dir is empty or llm is None
        """
        # Validate required parameters
        if not workspace_dir:
            raise ValueError("WriterAgent must be provided with workspace_dir parameter")
        if llm is None:
            raise ValueError("WriterAgent requires a valid LLM instance")
        
        self.llm = llm
        self.output_mode = output_mode.lower()
        self.workspace_dir = workspace_dir
        self.stream_manager = stream_manager
        self.work_id = work_id
        
        # Log LaTeX mode warning
        if self.output_mode == "latex":
            logger.warning(
                "WriterAgent initialized with output_mode='latex', but LaTeX is not yet supported. "
                "Please use 'word' or 'markdown' mode instead."
            )
        
        # Load tools based on output mode
        try:
            self.tools = self._load_tools()
            logger.info(
                "WriterAgent loaded %d tools for output_mode='%s'",
                len(self.tools),
                self.output_mode
            )
        except Exception as e:
            logger.error("Failed to load tools for WriterAgent: %s", e, exc_info=True)
            raise ValueError(f"Failed to load tools: {str(e)}")
        
        # Create LangChain agent
        try:
            self.agent = create_agent(
                model=self.llm,
                tools=self.tools,
                system_prompt=self._get_system_prompt(),
            )
            logger.info(
                "WriterAgent initialized successfully, workspace_dir: %s, work_id: %s, output_mode: %s, tools: %d",
                workspace_dir,
                work_id,
                self.output_mode,
                len(self.tools),
            )
        except Exception as e:
            logger.error("Failed to create WriterAgent: %s", e, exc_info=True)
            raise ValueError(f"Failed to create agent: {str(e)}")

    def _load_tools(self) -> List[BaseTool]:
        """
        Load appropriate tools based on output_mode.
        
        Returns:
            List of LangChain tools for the specified output mode
            
        Raises:
            ValueError: If output_mode is unsupported
        """
        if self.output_mode == "word":
            # Load Word tools
            word_tools = LangChainToolFactory.create_word_tools(
                workspace_dir=self.workspace_dir,
                stream_manager=self.stream_manager
            )
            if not word_tools:
                raise ValueError("Failed to create Word tools")
            return word_tools
            
        elif self.output_mode == "markdown":
            # Load Markdown tools (writemd, update_template)
            markdown_tools = LangChainToolFactory.create_file_tools(
                workspace_dir=self.workspace_dir,
                stream_manager=self.stream_manager
            )
            if not markdown_tools:
                raise ValueError("Failed to create Markdown tools")
            return markdown_tools
            
        elif self.output_mode == "latex":
            # LaTeX not yet supported - return empty list
            logger.warning("LaTeX mode requested but not yet supported")
            return []
            
        else:
            raise ValueError(
                f"Unsupported output_mode: '{self.output_mode}'. "
                f"Supported modes: 'word', 'markdown', 'latex'"
            )

    def _get_system_prompt(self) -> str:
        """
        Generate system prompt based on output_mode.
        
        Returns:
            System prompt string tailored to the output mode
        """
        base_prompt = (
            "你是一个专业的学术写作助手（WriterAgent），负责根据高层次的写作目标自主创作内容。\n"
            "**你使用的语言需要跟模板语言一致**\n\n"
            "**🎯 核心职责**：\n"
            "1. **理解写作目标**：MainAgent会给你高层次的写作目标（例如：\"写Introduction章节\"）\n"
            "2. **自主创作内容**：你需要根据目标自己思考并创作具体的文字内容\n"
            "3. **选择合适工具**：根据内容类型选择合适的文档工具完成操作\n"
            "4. **确保质量**：内容要专业、准确、符合学术规范\n\n"
            "**🚫 重要提醒**：\n"
            "- MainAgent只会告诉你\"写什么主题\"，不会告诉你\"写什么内容\"\n"
            "- 你需要自己扩充和发挥，创作具体的段落文字\n"
            "- 不要只是简单执行指令，要展现你的写作能力\n\n"
        )
        
        if self.output_mode == "word":
            has_template = (Path(self.workspace_dir) / ".system" / "_template_original.docx").exists()

            if has_template:
                prompt = base_prompt + (
                    "**输出模式：Word (.docx) — 模板填充模式**\n\n"
                    "⚠️ 当前工作空间存在用户上传的模板文件，你必须在模板基础上填充内容，\n"
                    "**绝对禁止**从零创建新文档覆盖模板！\n\n"

                    "你拥有以下工具：\n"
                    "1. **read_docx** — 读取文档纯文本内容\n"
                    "2. **get_template_structure** — 获取模板段落索引和结构（用于定位）\n"
                    "3. **write_to_template** — 在模板指定位置插入/替换内容（核心工具）\n"
                    "4. **repair_template_structure** — 当标题骨架被写坏时，按模板恢复标题文本/样式\n"
                    "5. **create_docx** — 仅用于创建paper.docx以外的附件文档\n"
                    "6. **edit_docx / repack_docx** — 低级 XML 编辑（极少使用）\n\n"

                    "**⚠️ 核心工作流程（模板模式）**\n"
                    "1. 调用 `get_template_structure()` 了解模板的段落编号、标题层级和占位内容\n"
                    "2. 调用 `read_docx()` 查看完整文本，理解已有内容\n"
                    "3. 确定要填充的位置（通过 anchor_text 精确匹配段落文本）\n"
                    "4. 调用 `write_to_template(anchor_text=..., content=..., position='after')` 逐处填充\n\n"

                    "**write_to_template 参数说明**：\n"
                    "- `anchor_text`: 用于定位的段落文本（部分匹配即可）\n"
                    "- `content`: 要插入的文字内容，多段用 \\n\\n 分隔；代码用 ``` 包裹\n"
                    "- `position`: 'after'（锚点后）/ 'before'（锚点前）/ 'replace'（替换锚点）\n"
                    "- `style`: 可选样式名如 'Normal', 'List Paragraph'\n\n"

                    "**工作流程示例**：\n"
                    "收到指令：\"完成第一章 DDL 的实验任务\"\n"
                    "1. `get_template_structure()` → 看到 [48] [Heading 4] 1.2.1 创建数据表\n"
                    "2. `read_docx()` → 看到 \"执行代码：\" 后面是空的\n"
                    "3. `write_to_template(anchor_text='1.2.1 创建数据表', content='...解释...', position='after')`\n"
                    "4. `write_to_template(anchor_text='执行代码：', content='```sql\\nCREATE TABLE...\\n```', position='after')`\n\n"

                    "**关键规则**：\n"
                    "- 只填充、不覆盖：保留模板所有已有标题、表格、占位栏目\n"
                    "- 按位填充：通过 anchor_text 精确定位要填充的位置\n"
                    "- 格式继承：模板中的字体、字号、行距由模板样式自动继承\n"
                    "- 代码块：用 ``` 包裹，自动使用 Courier New 10pt 等宽字体\n"
                    "- 如果 anchor 找不到，工具会返回可用段落列表，据此调整 anchor\n\n"

                    "**模板结构修复规则**：\n"
                    "- 如果审查提示“标题层级/顺序/文本与模板不一致”或标题里残留 Markdown 标记，先调用 `repair_template_structure()`\n"
                    "- 不要手工替换标题段落；标题必须由模板骨架决定，正文只能插入到标题或占位说明前后\n\n"

                    "**内容创作要求**：\n"
                    "- 每次只填充当前指令要求的章节内容\n"
                    "- 不要重复模板已有的标题和固定文字\n"
                    "- SQL 代码必须完整、可执行\n"
                    "- 解释性文字要充实，符合学术规范\n\n"
                )
            else:
                prompt = base_prompt + (
                    "**输出模式：Word (.docx) — 基于 docx-js**\n\n"
                    "你拥有以下工具：\n"
                "1. **create_docx** — 执行一段完整的 Node.js 脚本（使用 `require('docx')`）来生成 .docx 文件\n"
                "2. **read_docx** — 读取现有 .docx 文件的纯文本内容\n"
                "3. **edit_docx** — 解包已有 .docx 为 XML 结构（用于后续手动编辑）\n"
                "4. **repack_docx** — 将编辑后的 XML 重新打包为 .docx\n\n"

                "**⚠️ 核心工作流程**\n"
                "1. 如果文档已存在，先调用 `read_docx` 了解现有内容和模板骨架\n"
                "2. 如果存在模板契约，必须完整保留模板章节顺序、表格/占位栏位和显式格式要求\n"
                "3. 生成一段完整的 JS 脚本，调用 `create_docx` 一次性创建/重建整个文档\n"
                "4. 脚本中使用 `process.env.OUTPUT_PATH` 作为输出路径\n\n"

                "**JS 脚本模板**：\n"
                "```javascript\n"
                "const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,\n"
                "        ImageRun, Header, Footer, AlignmentType, HeadingLevel, LevelFormat,\n"
                "        BorderStyle, WidthType, ShadingType, PageBreak, PageNumber,\n"
                "        TableOfContents, ExternalHyperlink, FootnoteReferenceRun,\n"
                "        TabStopType, TabStopPosition } = require('docx');\n"
                "const fs = require('fs');\n\n"
                "const doc = new Document({\n"
                "  styles: {\n"
                "    default: { document: { run: { font: 'Arial', size: 24 } } },\n"
                "    paragraphStyles: [\n"
                "      { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true,\n"
                "        run: { size: 32, bold: true, font: 'Arial' },\n"
                "        paragraph: { spacing: { before: 240, after: 240 }, outlineLevel: 0 } },\n"
                "      { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true,\n"
                "        run: { size: 28, bold: true, font: 'Arial' },\n"
                "        paragraph: { spacing: { before: 180, after: 180 }, outlineLevel: 1 } },\n"
                "    ]\n"
                "  },\n"
                "  sections: [{\n"
                "    properties: {\n"
                "      page: {\n"
                "        size: { width: 12240, height: 15840 },\n"
                "        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }\n"
                "      }\n"
                "    },\n"
                "    children: [ /* 在此构建文档内容 */ ]\n"
                "  }]\n"
                "});\n"
                "Packer.toBuffer(doc).then(buf => fs.writeFileSync(process.env.OUTPUT_PATH, buf));\n"
                "```\n\n"

                "**关键规则**：\n"
                "- 模板优先：如果模板要求宋体、小三、居中、行距、页边距等格式，必须在 `styles`、`Paragraph`、`TextRun`、section properties 中显式设置\n"
                "- 骨架优先：模板中的章节、表格、固定栏目和占位提示必须完整保留并填充，不要改名、重排或省略\n"
                "- 换行：使用多个 `new Paragraph(...)` 而非 `\\n`\n"
                "- 列表：使用 `numbering.config` + `LevelFormat.BULLET`/`DECIMAL`，**禁止** unicode 符号\n"
                "- 表格：必须同时设 `columnWidths`（数组）和每个 cell 的 `width`，两者要匹配；使用 `WidthType.DXA`\n"
                "- 表格底纹：`ShadingType.CLEAR`（不是 SOLID）\n"
                "- 图片：如果工作区存在 `outputs/*.png`、`outputs/*.jpg` 或上游任务明确提供图片路径，必须在正文相关位置插入图片，不能只写“见图”或图片说明文字\n"
                "- 图片写法：`new ImageRun({ type: 'png', data: fs.readFileSync('outputs/chart.png'), transformation: {width: 520, height: 330}, altText: { title: '图表标题', description: '图表说明', name: 'chart' } })`\n"
                "- 图片路径：优先使用 MainAgent/CodeAgent 提供的正式输出路径，如 `outputs/pi_convergence.png`；如果只提供 run artifact 路径，也应使用该相对路径插入\n"
                "- 图片版式：图片段落使用 `alignment: AlignmentType.CENTER`，图片后紧跟一段居中的图题（例如“图1 蒙特卡洛估计π值的收敛过程”）\n"
                "- 分页：`new Paragraph({ children: [new PageBreak()] })`\n"
                "- TOC：标题必须用 `HeadingLevel.HEADING_1` 等，且 style 定义中包含 `outlineLevel`\n"
                "- 页眉/页脚：通过 `headers`/`footers` 属性在 section 中设置\n"
                "- 单位：DXA (1440 = 1英寸)，US Letter = 12240×15840 DXA\n\n"

                "**工作流程示例**：\n"
                "收到指令：\"写一个 Introduction 章节\"\n"
                "1. 调用 `read_docx()` 了解现有内容\n"
                "2. 构思 Introduction 内容（定义、背景、意义、本文贡献）\n"
                "3. 编写完整的 JS 脚本，包含所有已有内容 + 新增 Introduction\n"
                "4. 调用 `create_docx(js_code=<完整脚本>)` 一次性生成\n\n"

                "**内容创作要求**：\n"
                "- 段落要充实，每段至少3-5句话\n"
                "- 逻辑清晰，层次分明\n"
                "- 语言专业，符合学术规范\n"
                "- 每次生成的 JS 脚本应包含文档的完整内容（已有 + 新增）\n\n"
            )
            
        elif self.output_mode == "markdown":
            prompt = base_prompt + (
                "**输出模式：Markdown (.md)**\n\n"
                "你可以使用以下Markdown工具：\n"
                "1. get_paper_status - 获取论文写作状态概览（章节结构、进度、摘要）\n"
                "2. readmd - 读取Markdown文件完整内容\n"
                "3. writemd - 写入Markdown内容（支持多种模式）\n"
                "4. update_template - 更新模板中的特定章节\n\n"
                "**⚠️ 重要：开始写作前必须先了解文档当前状态**\n"
                "在进行任何写作操作之前，你必须首先调用 get_paper_status 或 readmd 来了解 paper.md 的现有内容和结构。\n"
                "**绝对禁止在不读取文件的情况下直接写入！**\n\n"
                "**writemd 写入模式选择指南（关键！）**：\n"
                "- **更新已有章节** → mode='section_update'（推荐！content必须以#标题开头，只替换该章节）\n"
                "- **添加全新章节** → mode='append'（在文件末尾追加，不影响已有内容）\n"
                "- **重建整个文件** → mode='overwrite'（⚠️危险！会删除所有已有内容，极少使用）\n\n"
                "**🔴 防止内容丢失的核心规则**：\n"
                "1. 每次写作前必须先调用 get_paper_status 查看当前状态\n"
                "2. 如果章节已有内容，使用 section_update 或 update_template 更新，不要用 overwrite\n"
                "3. 添加新章节时使用 append 模式，不要用 overwrite\n"
                "4. 只有在需要完全重建论文时才使用 overwrite 模式\n"
                "5. 写入的内容不要包含其他已存在章节的内容，只写当前目标章节\n\n"
                "**工作流程示例**：\n"
                "收到指令：\"写一个Introduction章节，介绍研究背景\"\n"
                "你应该：\n"
                "1. **首先获取论文状态**：\n"
                "   - 调用 get_paper_status() 查看哪些章节已写、哪些未写\n"
                "2. 思考：Introduction应该包含什么内容？\n"
                "3. 创作具体的Markdown内容\n"
                "4. **选择正确的写入模式**：\n"
                "   - 如果Introduction章节已存在但内容为空 → 用 update_template 或 writemd(mode='section_update')\n"
                "   - 如果Introduction章节不存在 → 用 writemd(mode='append')\n"
                "   - 如果需要重写Introduction → 用 writemd(mode='section_update')\n"
                "5. 确认完成并报告\n\n"
                "**数学公式渲染规则**：\n"
                "- **行内公式**：使用单个 $ 符号包裹，例如：$E = mc^2$\n"
                "- **独立行公式**：使用双 $$ 符号包裹，例如：\n"
                "  $$\n"
                "  R = \\sqrt{A^2 + B^2 + 2AB \\cos(\\phi)}\n"
                "  $$\n"
                "- **禁止使用**：不要使用 LaTeX 原生的 \\[ \\] 或 \\( \\) 分隔符，系统不支持\n"
                "- **特殊字符转义**：在公式中使用反斜杠时需要双反斜杠，如 \\\\sqrt, \\\\frac\n\n"
                "**内容创作要求**：\n"
                "- 如果 paper.md 来自模板，必须按模板已有标题层级、顺序、占位说明逐节填充，不要删除或重排骨架\n"
                "- 模板中出现的字体、字号、对齐、行距等格式要求应保留为 Markdown/HTML/说明性标记，不能忽略\n"
                "- 使用标准Markdown格式\n"
                "- 段落要充实，逻辑清晰\n"
                "- 适当使用标题层级（#, ##, ###）\n"
                "- 语言专业，符合学术规范\n"
                "- 数学公式必须使用 $ 或 $$ 分隔符\n\n"
            )
            
        elif self.output_mode == "latex":
            prompt = base_prompt + (
                "**Output Mode: LaTeX**\n\n"
                "LaTeX mode is not yet supported. Please inform the user to use 'word' or 'markdown' mode instead.\n"
            )
            
        else:
            prompt = base_prompt + "Unknown output mode. Please check configuration.\n"

        return self._append_template_contract(prompt)

    def _append_template_contract(self, prompt: str) -> str:
        template_contract = read_template_contract(self.workspace_dir)
        if not template_contract:
            return prompt
        return (
            prompt
            + "\n\n**📌 当前工作区模板契约（最高优先级写作约束）**\n"
            + "以下内容来自用户上传的模板骨架和格式要求，必须逐条遵循：\n\n"
            + template_contract
            + "\n"
        )

    async def run(self, instruction: str) -> str:
        """
        Execute a writing instruction by calling appropriate tools.
        
        Args:
            instruction: Natural language writing instruction specifying what to write
            
        Returns:
            Execution result message or error description
        """
        logger.info("WriterAgent starting task execution: %s", instruction[:100])
        
        # Send start notification
        if self.stream_manager:
            try:
                await self.stream_manager.send_json_block(
                    "writer_agent_start",
                    f"WriterAgent starting: {instruction[:100]}..."
                )
            except Exception as e:
                logger.warning("Failed to send WriterAgent start notification: %s", e)
        
        # Validate instruction
        if not instruction or not instruction.strip():
            error_msg = (
                "Error: Instruction validation failed: Empty instruction\n"
                "Details: Instruction must specify what content to write\n"
                "Suggestion: Provide instruction in format: 'Add [type] with content: [text]'"
            )
            logger.error("WriterAgent received empty instruction")
            return error_msg
        
        # Execute instruction
        try:
            inputs = {"messages": [HumanMessage(content=instruction)]}
            result = await self.agent.ainvoke(inputs, config={"recursion_limit": 100})
            output = self._extract_output(result)
            
            # Send completion notification
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "writer_agent_result",
                        output
                    )
                except Exception as e:
                    logger.warning("Failed to send WriterAgent completion notification: %s", e)
            
            logger.info("WriterAgent task completed successfully")
            return output
            
        except Exception as e:
            # Format error message
            error_msg = self._format_error(e, instruction)
            logger.error("WriterAgent execution failed: %s", e, exc_info=True)
            
            # Send error notification
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "writer_agent_error",
                        f"WriterAgent execution failed: {str(e)}"
                    )
                except Exception:
                    pass
            
            return error_msg

    def _extract_output(self, result: Any) -> str:
        """
        Extract final output from agent execution result.
        
        Args:
            result: Agent execution result (typically a dict)
            
        Returns:
            Extracted output string
        """
        if isinstance(result, dict):
            # Try to get output field
            if result.get("output"):
                return str(result["output"])
            
            # Try to extract from messages
            messages = result.get("messages")
            if messages:
                for msg in reversed(messages):
                    content = getattr(msg, "content", None)
                    if not content and isinstance(msg, dict):
                        content = msg.get("content")
                    if content:
                        return str(content)
        
        return str(result)

    def _format_error(self, error: Exception, instruction: str) -> str:
        """
        Format error message with operation details and suggestions.
        
        Args:
            error: Exception that occurred
            instruction: Original instruction that failed
            
        Returns:
            Formatted error message string
        """
        error_type = type(error).__name__
        error_str = str(error)
        
        # Categorize error and provide suggestions
        if "file" in error_str.lower() and "not found" in error_str.lower():
            suggestion = "Ensure the file exists or generate it first using CodeAgent"
        elif "path" in error_str.lower() and "invalid" in error_str.lower():
            suggestion = "Check that the file path is valid and within the workspace"
        elif "unsupported" in error_str.lower() or "not supported" in error_str.lower():
            suggestion = "Use a supported output mode: 'word' or 'markdown'"
        else:
            suggestion = "Check the instruction format and try again, or contact support"
        
        return (
            f"Error: WriterAgent execution failed: {error_type}\n"
            f"Details: {error_str}\n"
            f"Instruction: {instruction[:200]}\n"
            f"Suggestion: {suggestion}"
        )

    def get_execution_summary(self) -> Dict[str, Any]:
        """
        Get execution summary with agent metadata.
        
        Returns:
            Dictionary containing agent type, configuration, and tool information
        """
        return {
            "agent_type": "WriterAgent",
            "output_mode": self.output_mode,
            "workspace_dir": self.workspace_dir,
            "work_id": self.work_id,
            "tools_count": len(self.tools),
            "tool_names": [tool.name for tool in self.tools],
            "langchain_based": True,
        }
