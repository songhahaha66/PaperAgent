"""
Direct Word document tools wrapper
Wraps existing office_word_mcp tool functions with workspace context
"""

import logging
import asyncio
from typing import List, Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class WordTools:
    """
    Direct Word document tools wrapper
    Wraps existing office_word_mcp tool functions with workspace context
    """
    
    def __init__(self, workspace_dir: str, stream_manager=None):
        """
        Initialize Word tools
        
        Args:
            workspace_dir: Absolute path to workspace directory
            stream_manager: Optional stream manager for output notifications
        """
        self.workspace_dir = Path(workspace_dir).resolve()
        self.stream_manager = stream_manager
        self.document_path = str(self.workspace_dir / "paper.docx")
        
        logger.info(f"WordTools initialized for workspace: {self.workspace_dir}")
    
    def _resolve_path(self, relative_path: str) -> str:
        """
        Resolve relative path to absolute path within workspace
        
        Args:
            relative_path: Path relative to workspace or absolute path
            
        Returns:
            Absolute path string
            
        Raises:
            ValueError: If path is outside workspace
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
    
    def _send_notification(self, notification_type: str, message: str):
        """
        Send notification through stream manager
        
        Args:
            notification_type: Type of notification
            message: Notification message
        """
        if not self.stream_manager:
            return
        
        # Skip word_tool_result notifications
        if notification_type == "word_tool_result":
            return
        
        try:
            # Send notification asynchronously
            asyncio.create_task(
                self.stream_manager.send_json_block(notification_type, message)
            )
        except Exception as e:
            logger.warning(f"Failed to send notification: {e}")
            # Don't fail the operation due to notification failure
    
    def _handle_error(self, operation: str, error: Exception) -> str:
        """
        Centralized error handling
        
        Args:
            operation: Name of the operation that failed
            error: The exception that occurred
            
        Returns:
            User-friendly error message
        """
        error_msg = f"{operation} failed: {str(error)}"
        logger.error(error_msg, exc_info=True)
        
        # Send notification if stream manager available
        self._send_notification("error", error_msg)
        
        return error_msg
    
    # ===== Category 1: Document Creation and Properties =====
    
    async def create_document(self, title: Optional[str] = None, author: Optional[str] = None, overwrite: bool = False) -> str:
        """Create new Word document
        
        Args:
            title: Optional document title
            author: Optional document author
            overwrite: If True, overwrite existing document. If False (default), skip if file exists.
        """
        from word_document_server.tools import document_tools
        
        try:
            self._send_notification("word_tool_call", "Creating Word document")
            result = await document_tools.create_document(self.document_path, title, author, overwrite)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("create_document", e)
    
    async def get_document_info(self) -> str:
        """Get document metadata and properties"""
        from word_document_server.tools import document_tools
        
        try:
            self._send_notification("word_tool_call", "Getting document info")
            result = await document_tools.get_document_info(self.document_path)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("get_document_info", e)
    
    async def get_document_text(self) -> str:
        """Extract all text from document"""
        from word_document_server.tools import document_tools
        
        try:
            self._send_notification("word_tool_call", "Extracting document text")
            result = await document_tools.get_document_text(self.document_path)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("get_document_text", e)
    
    async def get_document_outline(self) -> str:
        """Get document structure"""
        from word_document_server.tools import document_tools
        
        try:
            self._send_notification("word_tool_call", "Getting document outline")
            result = await document_tools.get_document_outline(self.document_path)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("get_document_outline", e)
    
    async def list_available_documents(self) -> str:
        """List all .docx files in workspace"""
        from word_document_server.tools import document_tools
        
        try:
            self._send_notification("word_tool_call", "Listing available documents")
            result = await document_tools.list_available_documents(str(self.workspace_dir))
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("list_available_documents", e)
    
    async def copy_document(self, destination_filename: Optional[str] = None) -> str:
        """Create a copy of the document"""
        from word_document_server.tools import document_tools
        
        try:
            self._send_notification("word_tool_call", f"Copying document to {destination_filename}")
            result = await document_tools.copy_document(self.document_path, destination_filename)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("copy_document", e)
    
    # ===== Category 2: Content Addition =====
    
    async def add_heading(self, text: str, level: int = 1, font_name: Optional[str] = None,
                         font_size: Optional[int] = None, bold: Optional[bool] = None,
                         italic: Optional[bool] = None, border_bottom: bool = False) -> str:
        """Add heading to document"""
        from word_document_server.tools import content_tools
        
        try:
            self._send_notification("word_tool_call", f"Adding heading: {text}")
            result = await content_tools.add_heading(
                self.document_path, text, level, font_name, font_size, bold, italic, border_bottom
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("add_heading", e)
    
    async def add_paragraph(self, text: str, style: Optional[str] = None,
                           font_name: Optional[str] = None, font_size: Optional[int] = None,
                           bold: Optional[bool] = None, italic: Optional[bool] = None,
                           color: Optional[str] = None) -> str:
        """Add paragraph to document"""
        from word_document_server.tools import content_tools
        
        try:
            self._send_notification("word_tool_call", "Adding paragraph")
            result = await content_tools.add_paragraph(
                self.document_path, text, style, font_name, font_size, bold, italic, color
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("add_paragraph", e)
    
    async def add_table(self, rows: int, cols: int, data: Optional[List[List[str]]] = None) -> str:
        """Add table to document"""
        from word_document_server.tools import content_tools
        
        try:
            self._send_notification("word_tool_call", f"Adding table ({rows}x{cols})")
            result = await content_tools.add_table(self.document_path, rows, cols, data)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("add_table", e)
    
    async def add_picture(self, image_path: str, width: Optional[float] = None) -> str:
        """Add picture to document with path resolution"""
        from word_document_server.tools import content_tools
        
        try:
            # Resolve path relative to workspace
            resolved_path = self._resolve_path(image_path)
            
            self._send_notification("word_tool_call", f"Adding picture: {image_path}")
            result = await content_tools.add_picture(self.document_path, resolved_path, width)
            self._send_notification("word_tool_result", result)
            return result
        except ValueError as e:
            # Path security violation
            error_msg = f"Invalid image path: {str(e)}"
            logger.error(error_msg)
            self._send_notification("error", error_msg)
            return f"Error: {error_msg}"
        except Exception as e:
            return self._handle_error("add_picture", e)
    
    async def add_page_break(self) -> str:
        """Add page break to document"""
        from word_document_server.tools import content_tools
        
        try:
            self._send_notification("word_tool_call", "Adding page break")
            result = await content_tools.add_page_break(self.document_path)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("add_page_break", e)
    
    # ===== Category 3: Advanced Content Manipulation =====
    
    async def insert_header_near_text(self, target_text: Optional[str] = None, header_title: str = "",
                                     position: str = 'after', header_style: str = 'Heading 1',
                                     target_paragraph_index: Optional[int] = None) -> str:
        """Insert header near text"""
        from word_document_server.tools import content_tools
        
        try:
            self._send_notification("word_tool_call", f"Inserting header near text: {target_text}")
            result = await content_tools.insert_header_near_text_tool(
                self.document_path, target_text, header_title, position, header_style, target_paragraph_index
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("insert_header_near_text", e)
    
    async def insert_line_or_paragraph_near_text(self, target_text: Optional[str] = None, line_text: str = "",
                                                position: str = 'after', line_style: Optional[str] = None,
                                                target_paragraph_index: Optional[int] = None) -> str:
        """Insert line or paragraph near text"""
        from word_document_server.tools import content_tools
        
        try:
            self._send_notification("word_tool_call", f"Inserting line near text: {target_text}")
            result = await content_tools.insert_line_or_paragraph_near_text_tool(
                self.document_path, target_text, line_text, position, line_style, target_paragraph_index
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("insert_line_or_paragraph_near_text", e)
    
    async def insert_numbered_list_near_text(self, target_text: Optional[str] = None, list_items: Optional[list] = None,
                                            position: str = 'after', target_paragraph_index: Optional[int] = None,
                                            bullet_type: str = 'bullet') -> str:
        """Insert numbered list near text"""
        from word_document_server.tools import content_tools
        
        try:
            self._send_notification("word_tool_call", f"Inserting list near text: {target_text}")
            result = await content_tools.insert_numbered_list_near_text_tool(
                self.document_path, target_text, list_items, position, target_paragraph_index, bullet_type
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("insert_numbered_list_near_text", e)
    
    # ===== Category 4: Content Extraction =====
    
    async def get_paragraph_text_from_document(self, paragraph_index: int) -> str:
        """Get text from specific paragraph"""
        from word_document_server.tools import extended_document_tools
        
        try:
            self._send_notification("word_tool_call", f"Getting paragraph {paragraph_index}")
            result = await extended_document_tools.get_paragraph_text_from_document(self.document_path, paragraph_index)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("get_paragraph_text_from_document", e)
    
    async def find_text_in_document(self, text_to_find: str, match_case: bool = True, whole_word: bool = False) -> str:
        """Find text in document"""
        from word_document_server.tools import extended_document_tools
        
        try:
            self._send_notification("word_tool_call", f"Finding text: {text_to_find}")
            result = await extended_document_tools.find_text_in_document(
                self.document_path, text_to_find, match_case, whole_word
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("find_text_in_document", e)
    
    # ===== Category 5: Text Formatting =====
    
    async def format_text(self, paragraph_index: int, start_pos: int, end_pos: int,
                         bold: Optional[bool] = None, italic: Optional[bool] = None,
                         underline: Optional[bool] = None, color: Optional[str] = None,
                         font_size: Optional[int] = None, font_name: Optional[str] = None) -> str:
        """Format text in paragraph"""
        from word_document_server.tools import format_tools
        
        try:
            self._send_notification("word_tool_call", f"Formatting text in paragraph {paragraph_index}")
            result = await format_tools.format_text(
                self.document_path, paragraph_index, start_pos, end_pos,
                bold, italic, underline, color, font_size, font_name
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("format_text", e)
    
    async def search_and_replace(self, find_text: str, replace_text: str) -> str:
        """Search and replace text"""
        from word_document_server.tools import content_tools
        
        try:
            self._send_notification("word_tool_call", f"Searching and replacing: {find_text}")
            result = await content_tools.search_and_replace(self.document_path, find_text, replace_text)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("search_and_replace", e)
    
    async def delete_paragraph(self, paragraph_index: int) -> str:
        """Delete paragraph from document"""
        from word_document_server.tools import content_tools
        
        try:
            self._send_notification("word_tool_call", f"Deleting paragraph {paragraph_index}")
            result = await content_tools.delete_paragraph(self.document_path, paragraph_index)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("delete_paragraph", e)
    
    async def create_custom_style(self, style_name: str, bold: Optional[bool] = None,
                                 italic: Optional[bool] = None, font_size: Optional[int] = None,
                                 font_name: Optional[str] = None, color: Optional[str] = None,
                                 base_style: Optional[str] = None) -> str:
        """Create custom style"""
        from word_document_server.tools import format_tools
        
        try:
            self._send_notification("word_tool_call", f"Creating custom style: {style_name}")
            result = await format_tools.create_custom_style(
                self.document_path, style_name, bold, italic, font_size, font_name, color, base_style
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("create_custom_style", e)
    
    # ===== Category 6: Table Formatting =====
    
    async def format_table(self, table_index: int, has_header_row: Optional[bool] = None,
                          border_style: Optional[str] = None, shading: Optional[List[List[str]]] = None) -> str:
        """Format table"""
        from word_document_server.tools import format_tools
        
        try:
            self._send_notification("word_tool_call", f"Formatting table {table_index}")
            result = await format_tools.format_table(
                self.document_path, table_index, has_header_row, border_style, shading
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("format_table", e)
    
    async def set_table_cell_shading(self, table_index: int, row_index: int, col_index: int,
                                    fill_color: str, pattern: str = "clear") -> str:
        """Set table cell shading"""
        from word_document_server.tools import format_tools
        
        try:
            self._send_notification("word_tool_call", f"Setting cell shading for table {table_index}")
            result = await format_tools.set_table_cell_shading(
                self.document_path, table_index, row_index, col_index, fill_color, pattern
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("set_table_cell_shading", e)
    
    async def merge_table_cells(self, table_index: int, start_row: int, start_col: int,
                               end_row: int, end_col: int) -> str:
        """Merge table cells"""
        from word_document_server.tools import format_tools
        
        try:
            self._send_notification("word_tool_call", f"Merging cells in table {table_index}")
            result = await format_tools.merge_table_cells(
                self.document_path, table_index, start_row, start_col, end_row, end_col
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("merge_table_cells", e)
    
    async def set_table_cell_alignment(self, table_index: int, row_index: int, col_index: int,
                                      horizontal: str = "left", vertical: str = "top") -> str:
        """Set table cell alignment"""
        from word_document_server.tools import format_tools
        
        try:
            self._send_notification("word_tool_call", f"Setting cell alignment for table {table_index}")
            result = await format_tools.set_table_cell_alignment(
                self.document_path, table_index, row_index, col_index, horizontal, vertical
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("set_table_cell_alignment", e)
    
    async def set_table_column_width(self, table_index: int, col_index: int, width: float,
                                    width_type: str = "points") -> str:
        """Set table column width"""
        from word_document_server.tools import format_tools
        
        try:
            self._send_notification("word_tool_call", f"Setting column width for table {table_index}")
            result = await format_tools.set_table_column_width(
                self.document_path, table_index, col_index, width, width_type
            )
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("set_table_column_width", e)
    
    # ===== Category 7: Comment Extraction =====
    
    async def get_all_comments(self) -> str:
        """Get all comments from document"""
        from word_document_server.tools import comment_tools
        
        try:
            self._send_notification("word_tool_call", "Getting all comments")
            result = await comment_tools.get_all_comments(self.document_path)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("get_all_comments", e)
    
    async def get_comments_by_author(self, author: str) -> str:
        """Get comments by author"""
        from word_document_server.tools import comment_tools
        
        try:
            self._send_notification("word_tool_call", f"Getting comments by author: {author}")
            result = await comment_tools.get_comments_by_author(self.document_path, author)
            self._send_notification("word_tool_result", result)
            return result
        except Exception as e:
            return self._handle_error("get_comments_by_author", e)
    
    def __repr__(self) -> str:
        """String representation"""
        return f"WordTools(workspace={self.workspace_dir})"
