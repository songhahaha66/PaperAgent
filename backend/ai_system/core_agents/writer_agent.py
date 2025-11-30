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
            "You are a specialized document writing assistant. "
            "Your role is to execute structured writing instructions by calling the appropriate tools. "
            "Always ensure operations complete successfully before reporting completion.\n\n"
        )
        
        if self.output_mode == "word":
            return base_prompt + (
                "**Output Mode: Word (.docx)**\n\n"
                "You have access to Word document tools. Use them to:\n"
                "1. Create and modify Word documents (paper.docx)\n"
                "2. Add headings with word_add_heading (levels 1-5)\n"
                "3. Add paragraphs with word_add_paragraph\n"
                "4. Add tables with word_add_table\n"
                "5. Add images with word_add_picture\n"
                "6. Insert page breaks with word_add_page_break\n"
                "7. Format text and tables as needed\n\n"
                "Work systematically:\n"
                "- Parse the instruction to understand what content to write\n"
                "- Call the appropriate Word tools in sequence\n"
                "- Verify operations succeed by checking tool responses\n"
                "- Report any errors clearly with details\n"
                "- Confirm when all operations complete successfully\n"
            )
            
        elif self.output_mode == "markdown":
            return base_prompt + (
                "**Output Mode: Markdown (.md)**\n\n"
                "You have access to Markdown tools. Use them to:\n"
                "1. Write content with writemd tool (modes: append, overwrite, modify, insert, section_update)\n"
                "2. Update template sections with update_template tool\n"
                "3. Manage document structure and sections\n\n"
                "Work systematically:\n"
                "- Parse the instruction to understand what content to write\n"
                "- Choose the appropriate writemd mode or use update_template\n"
                "- Call the tools with correct parameters\n"
                "- Verify operations succeed by checking tool responses\n"
                "- Report any errors clearly with details\n"
                "- Confirm when all operations complete successfully\n"
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
            result = await self.agent.ainvoke(inputs)
            output = self._extract_output(result)
            
            # Send completion notification
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "writer_agent_result",
                        f"WriterAgent task completed, result length: {len(output)}"
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
