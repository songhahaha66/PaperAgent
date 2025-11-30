"""
Word Tool Wrapper for Office-Word-MCP-Server integration
Wraps MCP Word tools with workspace-specific path resolution and error handling
"""

import logging
import asyncio
from typing import List, Optional, Dict, Any
from pathlib import Path
from langchain_core.tools import StructuredTool, BaseTool

logger = logging.getLogger(__name__)


class WordToolWrapper:
    """
    Wraps MCP Word tools with workspace-specific path resolution
    
    This wrapper:
    - Converts relative paths to absolute paths within workspace
    - Validates file existence and security
    - Adds error handling and timeout management
    - Streams tool execution status to frontend
    """

    def __init__(self, mcp_client, workspace_dir: str, stream_manager=None):
        """Initialize Word tool wrapper
        
        Args:
            mcp_client: MCP client instance from MCPClientManager
            workspace_dir: Absolute path to workspace directory
            stream_manager: Stream manager for output
        """
        self.mcp_client = mcp_client
        self.workspace_dir = Path(workspace_dir).resolve()
        self.stream_manager = stream_manager
        self.session_context = None
        self.session = None
        self.base_tools = []
        
        logger.info(f"WordToolWrapper initialized for workspace: {self.workspace_dir}")

    async def initialize(self) -> bool:
        """Initialize MCP session and load tools
        
        Returns:
            bool: True if initialization successful
        """
        try:
            if not self.mcp_client:
                logger.error("MCP client not available")
                return False
            
            # Create session context manager
            self.session_context = self.mcp_client.session("word")
            
            # Enter the session context and get the actual session
            self.session = await self.session_context.__aenter__()
            
            # Load tools from MCP
            from langchain_mcp_adapters.tools import load_mcp_tools
            self.base_tools = await load_mcp_tools(self.session)
            
            logger.info(f"Loaded {len(self.base_tools)} Word tools from MCP")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Word tools: {e}", exc_info=True)
            return False

    async def cleanup(self) -> None:
        """Cleanup MCP session"""
        try:
            if self.session_context:
                await self.session_context.__aexit__(None, None, None)
                self.session_context = None
                self.session = None
                logger.info("Word tool session cleaned up")
        except Exception as e:
            logger.error(f"Error during Word tool cleanup: {e}", exc_info=True)

    def _resolve_path(self, relative_path: str) -> str:
        """Convert relative path to absolute path within workspace
        
        Args:
            relative_path: Path relative to workspace (e.g., "outputs/chart.png")
            
        Returns:
            Absolute path string
            
        Raises:
            ValueError: If path is outside workspace or invalid
        """
        try:
            # Convert to Path object
            path = Path(relative_path)
            
            # If already absolute, validate it's within workspace
            if path.is_absolute():
                resolved = path.resolve()
            else:
                # Resolve relative to workspace
                resolved = (self.workspace_dir / path).resolve()
            
            # Security check: ensure path is within workspace
            try:
                resolved.relative_to(self.workspace_dir)
            except ValueError:
                raise ValueError(f"Path {relative_path} is outside workspace")
            
            return str(resolved)
            
        except Exception as e:
            logger.error(f"Path resolution failed for {relative_path}: {e}")
            raise

    def _validate_file_exists(self, path: str) -> bool:
        """Check if file exists before using it
        
        Args:
            path: Absolute file path
            
        Returns:
            bool: True if file exists
        """
        return Path(path).exists()

    def _sanitize_path(self, path: str) -> str:
        """Sanitize and normalize path
        
        Args:
            path: Input path
            
        Returns:
            Sanitized path string
        """
        # Remove any dangerous path components
        path = path.replace("..", "")
        
        # Normalize path separators
        path = Path(path).as_posix()
        
        return path

    def create_langchain_tools(self) -> List[BaseTool]:
        """Create LangChain-compatible tools with workspace context
        
        Returns:
            List of wrapped Word tools
        """
        wrapped_tools = []
        
        # Define tool wrappers with path resolution
        tools_config = [
            {
                "name": "word_create_document",
                "description": "Create a new Word document in memory. This must be called before adding any content.",
                "func": self._word_create_document,
                "args_schema": None
            },
            {
                "name": "word_add_heading",
                "description": "Add a heading to the Word document with specified level (1-5). Level 1 is the largest heading.",
                "func": self._word_add_heading,
                "args_schema": {
                    "text": {"type": "string", "description": "Heading text"},
                    "level": {"type": "integer", "description": "Heading level (1-5)", "default": 1}
                }
            },
            {
                "name": "word_add_paragraph",
                "description": "Add a paragraph of text to the Word document.",
                "func": self._word_add_paragraph,
                "args_schema": {
                    "text": {"type": "string", "description": "Paragraph text"}
                }
            },
            {
                "name": "word_add_table",
                "description": "Add a table to the Word document with specified rows and columns.",
                "func": self._word_add_table,
                "args_schema": {
                    "rows": {"type": "integer", "description": "Number of rows"},
                    "cols": {"type": "integer", "description": "Number of columns"},
                    "data": {"type": "array", "description": "Table data as list of lists (optional)"}
                }
            },
            {
                "name": "word_add_picture",
                "description": "Insert an image into the Word document from a file path. Path can be relative to workspace.",
                "func": self._word_add_picture,
                "args_schema": {
                    "image_path": {"type": "string", "description": "Path to image file (relative to workspace or absolute)"},
                    "width": {"type": "number", "description": "Image width in inches", "default": 6.0}
                }
            },
            {
                "name": "word_save_document",
                "description": "Save the Word document to the workspace as paper.docx.",
                "func": self._word_save_document,
                "args_schema": None
            }
        ]
        
        # Create StructuredTool for each wrapper
        for config in tools_config:
            try:
                tool = StructuredTool.from_function(
                    coroutine=config["func"],
                    name=config["name"],
                    description=config["description"]
                )
                wrapped_tools.append(tool)
                logger.debug(f"Created wrapped tool: {config['name']}")
            except Exception as e:
                logger.error(f"Failed to create tool {config['name']}: {e}")
        
        logger.info(f"Created {len(wrapped_tools)} wrapped Word tools")
        return wrapped_tools

    async def _execute_word_tool(self, tool_name: str, timeout: int = 30, **kwargs) -> str:
        """Execute Word tool with error handling and timeout
        
        Args:
            tool_name: Name of the MCP tool to call
            timeout: Timeout in seconds (default 30)
            **kwargs: Tool arguments
            
        Returns:
            Tool execution result
            
        Raises:
            TimeoutError: If tool execution times out
            Exception: If tool execution fails
        """
        try:
            # Stream tool call to frontend
            if self.stream_manager:
                await self.stream_manager.send_json_block(
                    "word_tool_call",
                    f"Calling {tool_name} with args: {kwargs}"
                )
            
            logger.info(f"Executing Word tool: {tool_name} with args: {kwargs}")
            
            # Find the tool in base_tools
            tool = None
            for t in self.base_tools:
                if t.name == tool_name:
                    tool = t
                    break
            
            if not tool:
                raise ValueError(f"Tool {tool_name} not found in MCP tools")
            
            # Execute with timeout
            result = await asyncio.wait_for(
                tool.ainvoke(kwargs),
                timeout=timeout
            )
            
            # Stream result to frontend
            if self.stream_manager:
                await self.stream_manager.send_json_block(
                    "word_tool_result",
                    f"{tool_name} completed: {result}"
                )
            
            logger.info(f"Word tool {tool_name} completed successfully")
            return str(result)
            
        except asyncio.TimeoutError:
            error_msg = f"Word tool {tool_name} timed out after {timeout} seconds"
            logger.error(error_msg)
            
            if self.stream_manager:
                await self.stream_manager.send_json_block("word_tool_error", error_msg)
            
            raise TimeoutError(error_msg)
            
        except Exception as e:
            error_msg = f"Word tool {tool_name} failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            if self.stream_manager:
                await self.stream_manager.send_json_block("word_tool_error", error_msg)
            
            raise

    # Tool wrapper methods
    
    async def _word_create_document(self) -> str:
        """Create new Word document"""
        return await self._execute_word_tool("word_create_document")

    async def _word_add_heading(self, text: str, level: int = 1) -> str:
        """Add heading to Word document"""
        return await self._execute_word_tool(
            "word_add_heading",
            text=text,
            level=level
        )

    async def _word_add_paragraph(self, text: str) -> str:
        """Add paragraph to Word document"""
        return await self._execute_word_tool(
            "word_add_paragraph",
            text=text
        )

    async def _word_add_table(self, rows: int, cols: int, data: Optional[List[List[str]]] = None) -> str:
        """Add table to Word document"""
        kwargs = {"rows": rows, "cols": cols}
        if data:
            kwargs["data"] = data
        return await self._execute_word_tool("word_add_table", **kwargs)

    async def _word_add_picture(self, image_path: str, width: float = 6.0) -> str:
        """Add picture to Word document with path resolution"""
        try:
            # Sanitize path first
            image_path = self._sanitize_path(image_path)
            
            # Resolve to absolute path
            absolute_path = self._resolve_path(image_path)
            
            # Validate file exists
            if not self._validate_file_exists(absolute_path):
                warning_msg = f"Image file not found: {image_path}, skipping"
                logger.warning(warning_msg)
                
                if self.stream_manager:
                    await self.stream_manager.send_json_block("warning", warning_msg)
                
                return f"Warning: {warning_msg}"
            
            # Execute with absolute path
            return await self._execute_word_tool(
                "word_add_picture",
                image_path=absolute_path,
                width=width
            )
            
        except ValueError as e:
            # Path security violation
            error_msg = f"Invalid image path: {str(e)}"
            logger.error(error_msg)
            
            if self.stream_manager:
                await self.stream_manager.send_json_block("error", error_msg)
            
            return f"Error: {error_msg}"

    async def _word_save_document(self) -> str:
        """Save Word document to workspace"""
        try:
            # Determine output path (workspace root / paper.docx)
            output_path = str(self.workspace_dir / "paper.docx")
            
            logger.info(f"Saving Word document to: {output_path}")
            
            # Execute save
            result = await self._execute_word_tool(
                "word_save_document",
                output_path=output_path
            )
            
            # Stream success notification
            if self.stream_manager:
                await self.stream_manager.send_json_block(
                    "word_document_saved",
                    f"Word document saved to paper.docx"
                )
            
            return result
            
        except Exception as e:
            error_msg = f"Failed to save Word document: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            if self.stream_manager:
                await self.stream_manager.send_json_block("error", error_msg)
            
            raise

    def __repr__(self) -> str:
        """String representation"""
        return f"WordToolWrapper(workspace={self.workspace_dir}, tools={len(self.base_tools)})"
