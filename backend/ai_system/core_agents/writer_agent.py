"""
WriterAgent - Specialized agent for document writing operations
Handles Word and Markdown document generation by directly calling appropriate tools
"""

import logging
from typing import Any, Dict, Optional, List

from langchain.agents import create_agent
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool

from ..core_managers.langchain_tools import LangChainToolFactory

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
            return base_prompt + (
                "**输出模式：Word (.docx)**\n\n"
                "你可以使用以下Word工具：\n"
                "1. word_get_document_text - 提取文档全文内容\n"
                "2. word_add_heading - 添加标题（1-5级）\n"
                "3. word_add_paragraph - 添加段落\n"
                "4. word_add_table - 添加表格\n"
                "5. word_add_picture - 插入图片（⚠️ width参数单位是英寸，典型值3-6，如width=5.0表示5英寸宽）\n"
                "6. word_add_page_break - 插入分页符\n"
                "7. 其他格式化工具\n\n"
                "**⚠️ 重要：开始写作前必须先读取文档**\n"
                "在进行任何写作操作之前，你必须首先调用 word_get_document_text 来提取并理解现有文档的内容和结构。\n\n"
                "**工作流程示例**：\n"
                "收到指令：\"写一个Introduction章节，介绍圆周率的重要性\"\n"
                "你应该：\n"
                "1. **首先提取文档内容**：\n"
                "   - 调用 word_get_document_text() 获取现有文档内容\n"
                "   - 理解文档当前结构和已有内容\n"
                "2. 思考：Introduction应该包含什么内容？\n"
                "   - 圆周率的定义\n"
                "   - 历史重要性\n"
                "   - 现代应用价值\n"
                "   - 本文研究意义\n"
                "3. 创作具体内容：\n"
                "   - 调用 word_add_heading(\"Introduction\", level=1)\n"
                "   - 调用 word_add_paragraph(\"圆周率π是数学中最重要的常数之一...\")\n"
                "   - 调用 word_add_paragraph(\"自古以来，人类对圆周率的研究...\")\n"
                "   - 调用 word_add_paragraph(\"本文旨在探讨...\")\n"
                "4. 确认完成并报告\n\n"
                "**内容创作要求**：\n"
                "- 段落要充实，每段至少3-5句话\n"
                "- 逻辑清晰，层次分明\n"
                "- 语言专业，符合学术规范\n"
                "- 适当使用过渡句连接段落\n"
                "- 如果需要，可以添加多个段落来充分展开主题\n\n"
            )
            
        elif self.output_mode == "markdown":
            return base_prompt + (
                "**输出模式：Markdown (.md)**\n\n"
                "你可以使用以下Markdown工具：\n"
                "1. writemd - 写入Markdown内容（支持append、overwrite、modify等模式）\n"
                "2. update_template - 更新模板章节\n\n"
                "**工作流程示例**：\n"
                "收到指令：\"写一个Introduction章节，介绍研究背景\"\n"
                "你应该：\n"
                "1. 思考：Introduction应该包含什么内容？\n"
                "2. 创作具体的Markdown内容：\n"
                "   ```markdown\n"
                "   # Introduction\n"
                "   \n"
                "   研究背景的第一段...\n"
                "   \n"
                "   研究背景的第二段...\n"
                "   \n"
                "   本文的研究意义...\n"
                "   ```\n"
                "3. 调用 writemd 或 update_template 工具写入\n"
                "4. 确认完成并报告\n\n"
                "**数学公式渲染规则**：\n"
                "- **行内公式**：使用单个 $ 符号包裹，例如：$E = mc^2$\n"
                "- **独立行公式**：使用双 $$ 符号包裹，例如：\n"
                "  $$\n"
                "  R = \\sqrt{A^2 + B^2 + 2AB \\cos(\\phi)}\n"
                "  $$\n"
                "- **禁止使用**：不要使用 LaTeX 原生的 \\[ \\] 或 \\( \\) 分隔符，系统不支持\n"
                "- **特殊字符转义**：在公式中使用反斜杠时需要双反斜杠，如 \\\\sqrt, \\\\frac\n\n"
                "**内容创作要求**：\n"
                "- 使用标准Markdown格式\n"
                "- 段落要充实，逻辑清晰\n"
                "- 适当使用标题层级（#, ##, ###）\n"
                "- 语言专业，符合学术规范\n"
                "- 数学公式必须使用 $ 或 $$ 分隔符\n\n"
            )
            
        elif self.output_mode == "latex":
            return base_prompt + (
                "**Output Mode: LaTeX**\n\n"
                "LaTeX mode is not yet supported. Please inform the user to use 'word' or 'markdown' mode instead.\n"
            )
            
        else:
            return base_prompt + "Unknown output mode. Please check configuration.\n"

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
