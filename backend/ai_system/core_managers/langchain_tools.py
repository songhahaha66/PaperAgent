"""
LangChain 工具工厂
将现有自定义工具包装为 LangChain 标准格式，统一工具管理
"""

import os
import logging
from typing import List, Optional, Dict, Any
from langchain_core.tools import StructuredTool, BaseTool
from langchain_community.tools import DuckDuckGoSearchRun

# 导入现有的自定义工具
from ..core_tools.file_tools import FileTools
from ..core_tools.template_tools import TemplateAgentTools

logger = logging.getLogger(__name__)


class LangChainToolFactory:
    """
    LangChain 工具工厂
    将现有的自定义工具包装为 LangChain 标准格式
    """

    @staticmethod
    def create_file_tools(workspace_dir: str, stream_manager=None) -> List[StructuredTool]:
        """
        基于现有 FileTools 创建 LangChain 工具

        Args:
            workspace_dir: 工作空间目录
            stream_manager: 流式输出管理器

        Returns:
            LangChain 格式的文件工具列表
        """
        try:
            # 设置环境变量供 FileTools 使用
            os.environ["WORKSPACE_DIR"] = workspace_dir
            file_tools = FileTools(stream_manager)

            tools = [
                StructuredTool.from_function(
                    func=file_tools.writemd,
                    name="writemd",
                    description="写入Markdown文件到工作空间。支持多种模式：append(追加)、overwrite(覆盖)、modify(修改)、insert(插入)、section_update(章节更新)",
                    args_schema={
                        "filename": {"type": "string", "description": "文件名（不需要.md后缀）"},
                        "content": {"type": "string", "description": "Markdown内容"},
                        "mode": {"type": "string", "description": "写入模式：append/overwrite/modify/insert/section_update/smart_replace", "default": "overwrite"}
                    }
                ),
                StructuredTool.from_function(
                    func=file_tools.update_template,
                    name="update_template",
                    description="专门用于更新论文模板的特定章节，只支持章节级别的更新",
                    args_schema={
                        "template_name": {"type": "string", "description": "论文文件名，默认为paper.md", "default": "paper.md"},
                        "content": {"type": "string", "description": "要更新的章节内容"},
                        "section": {"type": "string", "description": "要更新的章节名称（必需）"}
                    }
                ),
                StructuredTool.from_function(
                    func=file_tools.tree,
                    name="tree",
                    description="显示工作空间的目录树结构，帮助了解文件组织",
                    args_schema={
                        "directory": {"type": "string", "description": "要显示的目录路径（可选，默认显示整个工作空间）"}
                    }
                ),
                StructuredTool.from_function(
                    func=file_tools.list_attachments,
                    name="list_attachments",
                    description="列出工作空间中所有附件文件，包括文件类型、大小等信息"
                )
            ]

            logger.info(f"创建了 {len(tools)} 个文件工具")
            return tools

        except Exception as e:
            logger.error(f"创建文件工具失败: {e}")
            return []

    @staticmethod
    def create_code_agent_tool(workspace_dir: str, stream_manager=None, llm=None) -> Optional[StructuredTool]:
        """
        创建 LangChain CodeAgent 代码执行工具

        Args:
            workspace_dir: 工作空间目录
            stream_manager: 流式输出管理器
            llm: LangChain LLM实例

        Returns:
            CodeAgent 工具或 None
        """
        try:
            if llm is None:
                logger.error("创建CodeAgent工具失败：未提供LLM实例")
                return None

            from ..core_agents.code_agent import CodeAgent

            code_agent = CodeAgent(
                llm=llm,
                stream_manager=stream_manager,
                workspace_dir=workspace_dir,
            )

            async def run_code_agent(task_description: str) -> str:
                try:
                    return await code_agent.run(task_description)
                except Exception as e:
                    logger.error(f"CodeAgent 工具执行失败: {e}", exc_info=True)
                    return f"CodeAgent 执行失败: {str(e)}"

            tool = StructuredTool.from_function(
                coroutine=run_code_agent,
                name="code_agent_execute",
                description="使用专用CodeAgent执行代码任务（生成、执行、修改、列出文件）。输入详细的代码需求或分析任务描述。",
            )

            logger.info("创建了 LangChain CodeAgent 工具")
            return tool

        except Exception as e:
            logger.error(f"创建 CodeAgent 工具失败: {e}")
            return None

    @staticmethod
    def create_writer_agent_tool(
        workspace_dir: str,
        output_mode: str,
        stream_manager=None,
        llm=None
    ) -> Optional[StructuredTool]:
        """
        Create LangChain WriterAgent tool for document writing operations
        
        The WriterAgent is a specialized agent that handles document writing by directly
        calling Word or Markdown tools based on the output mode. This allows the MainAgent
        to delegate document writing tasks through a single tool interface.

        Args:
            workspace_dir: Workspace directory path
            output_mode: Document format mode ("word", "markdown", or "latex")
            stream_manager: Stream manager for output notifications
            llm: LangChain LLM instance for WriterAgent

        Returns:
            WriterAgent tool or None if creation fails
        """
        try:
            if llm is None:
                logger.error("创建WriterAgent工具失败：未提供LLM实例")
                return None
            
            if not workspace_dir:
                logger.error("创建WriterAgent工具失败：未提供workspace_dir")
                return None

            # Import WriterAgent
            from ..core_agents.writer_agent import WriterAgent

            # Instantiate WriterAgent with provided parameters
            try:
                writer_agent = WriterAgent(
                    llm=llm,
                    output_mode=output_mode,
                    workspace_dir=workspace_dir,
                    stream_manager=stream_manager,
                )
            except Exception as e:
                logger.error(f"WriterAgent 实例化失败: {e}", exc_info=True)
                return None

            # Create async wrapper function for WriterAgent.run()
            async def run_writer_agent(instruction: str) -> str:
                """
                Execute writing instruction through WriterAgent
                
                Args:
                    instruction: Natural language writing instruction
                    
                Returns:
                    Execution result or error message
                """
                try:
                    return await writer_agent.run(instruction)
                except Exception as e:
                    logger.error(f"WriterAgent 工具执行失败: {e}", exc_info=True)
                    return f"WriterAgent 执行失败: {str(e)}"

            # Create StructuredTool with clear description
            tool = StructuredTool.from_function(
                coroutine=run_writer_agent,
                name="writer_agent_execute",
                description=(
                    f"使用专用WriterAgent执行文档写作任务（当前模式: {output_mode}）。"
                    "WriterAgent可以处理各种文档操作，包括添加标题、段落、表格、图片、分页符等。"
                    "输入详细的写作指令，例如：'添加一级标题Introduction'、'添加段落内容：...'、"
                    "'添加3行4列的表格，包含以下数据：...'、'插入图片outputs/chart.png，宽度6英寸'。"
                    "WriterAgent会自动选择合适的工具来完成任务。"
                ),
            )

            logger.info(f"创建了 LangChain WriterAgent 工具 (output_mode: {output_mode})")
            return tool

        except Exception as e:
            logger.error(f"创建 WriterAgent 工具失败: {e}", exc_info=True)
            return None

    @staticmethod
    def create_template_tools(workspace_dir: str, stream_manager=None) -> List[StructuredTool]:
        """
        基于现有 TemplateAgentTools 创建 LangChain 工具

        Args:
            workspace_dir: 工作空间目录
            stream_manager: 流式输出管理器

        Returns:
            LangChain 格式的模板工具列表
        """
        try:
            template_tools = TemplateAgentTools(workspace_dir)

            tools = [
                StructuredTool.from_function(
                    func=template_tools.analyze_template,
                    name="analyze_template",
                    description="分析paper.md文件的模板结构，为AI提供模板概览"
                ),
                StructuredTool.from_function(
                    func=template_tools.get_section_content,
                    name="get_section_content",
                    description="获取paper.md文件中指定章节的内容",
                    args_schema={
                        "section_title": {"type": "string", "description": "章节标题"}
                    }
                ),
                StructuredTool.from_function(
                    func=template_tools.update_section_content,
                    name="update_section_content",
                    description="更新paper.md文件中指定章节的内容",
                    args_schema={
                        "section_title": {"type": "string", "description": "章节标题"},
                        "new_content": {"type": "string", "description": "新的章节内容"}
                    }
                ),
                StructuredTool.from_function(
                    func=template_tools.add_section,
                    name="add_section",
                    description="在paper.md文件末尾添加新章节",
                    args_schema={
                        "section_title": {"type": "string", "description": "新章节标题"},
                        "content": {"type": "string", "description": "章节内容", "default": ""}
                    }
                ),
                StructuredTool.from_function(
                    func=template_tools.rename_section_title,
                    name="rename_section_title",
                    description="修改paper.md文件中指定章节的标题",
                    args_schema={
                        "old_title": {"type": "string", "description": "原标题"},
                        "new_title": {"type": "string", "description": "新标题"}
                    }
                )
            ]

            logger.info(f"创建了 {len(tools)} 个模板工具")
            return tools

        except Exception as e:
            logger.error(f"创建模板工具失败: {e}")
            return []

    @staticmethod
    def create_word_tools(workspace_dir: str, stream_manager=None) -> List[StructuredTool]:
        """
        创建 Word 文档工具（直接调用）
        
        Tool Categories:
        1. Document Creation and Properties
        2. Content Addition
        3. Advanced Content Manipulation
        4. Content Extraction
        5. Text Formatting
        6. Table Formatting
        7. Comment Extraction

        Args:
            workspace_dir: 工作空间目录
            stream_manager: 流式输出管理器

        Returns:
            LangChain 格式的 Word 工具列表
        """
        try:
            from ..core_tools.word_tools import WordTools
            
            # Create WordTools instance
            word_tools = WordTools(workspace_dir, stream_manager)
            
            # Category 1: Document Creation and Properties
            doc_creation_tools = [
                StructuredTool.from_function(
                    coroutine=word_tools.create_document,
                    name="word_create_document",
                    description="[Document Creation] Create a new Word document with optional title and author"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.get_document_info,
                    name="word_get_document_info",
                    description="[Document Properties] Get document metadata and properties including title, author, and statistics"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.get_document_text,
                    name="word_get_document_text",
                    description="[Document Properties] Extract all text content from the document"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.get_document_outline,
                    name="word_get_document_outline",
                    description="[Document Properties] Get the document structure and outline showing headings hierarchy"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.list_available_documents,
                    name="word_list_available_documents",
                    description="[Document Properties] List all .docx files available in the workspace"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.copy_document,
                    name="word_copy_document",
                    description="[Document Creation] Create a copy of the current document with a new filename"
                ),
            ]
            
            # Category 2: Content Addition
            content_tools = [
                StructuredTool.from_function(
                    coroutine=word_tools.add_heading,
                    name="word_add_heading",
                    description="[Content Addition] Add a heading to the document with specified level (1-5) and optional formatting"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.add_paragraph,
                    name="word_add_paragraph",
                    description="[Content Addition] Add a paragraph to the document with optional style and formatting"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.add_table,
                    name="word_add_table",
                    description="[Content Addition] Add a table to the document with specified rows and columns, optionally filled with data"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.add_picture,
                    name="word_add_picture",
                    description="[Content Addition] Add an image to the document from a file path with optional width specification"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.add_page_break,
                    name="word_add_page_break",
                    description="[Content Addition] Insert a page break in the document"
                ),
            ]
            
            # Category 3: Advanced Content Manipulation
            manipulation_tools = [
                StructuredTool.from_function(
                    coroutine=word_tools.insert_header_near_text,
                    name="word_insert_header_near_text",
                    description="[Advanced Content Manipulation] Insert a header before or after specific text or paragraph index"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.insert_line_or_paragraph_near_text,
                    name="word_insert_line_or_paragraph_near_text",
                    description="[Advanced Content Manipulation] Insert a line or paragraph before or after specific text or paragraph index"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.insert_numbered_list_near_text,
                    name="word_insert_numbered_list_near_text",
                    description="[Advanced Content Manipulation] Insert a numbered or bulleted list before or after specific text or paragraph index"
                ),
            ]
            
            # Category 4: Content Extraction
            extraction_tools = [
                StructuredTool.from_function(
                    coroutine=word_tools.get_paragraph_text_from_document,
                    name="word_get_paragraph_text",
                    description="[Content Extraction] Get text content from a specific paragraph by index"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.find_text_in_document,
                    name="word_find_text",
                    description="[Content Extraction] Find text in the document with options for case-sensitive and whole-word matching"
                ),
            ]
            
            # Category 5: Text Formatting
            text_format_tools = [
                StructuredTool.from_function(
                    coroutine=word_tools.format_text,
                    name="word_format_text",
                    description="[Text Formatting] Format text in a specific paragraph with bold, italic, underline, color, font size, and font name"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.search_and_replace,
                    name="word_search_and_replace",
                    description="[Text Formatting] Search for text and replace it with new text throughout the document"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.delete_paragraph,
                    name="word_delete_paragraph",
                    description="[Text Formatting] Delete a specific paragraph from the document by index"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.create_custom_style,
                    name="word_create_custom_style",
                    description="[Text Formatting] Create a custom style with specified formatting options"
                ),
            ]
            
            # Category 6: Table Formatting
            table_format_tools = [
                StructuredTool.from_function(
                    coroutine=word_tools.format_table,
                    name="word_format_table",
                    description="[Table Formatting] Format a table with header row, border style, and cell shading"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.set_table_cell_shading,
                    name="word_set_table_cell_shading",
                    description="[Table Formatting] Set shading color for a specific table cell"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.merge_table_cells,
                    name="word_merge_table_cells",
                    description="[Table Formatting] Merge a range of cells in a table"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.set_table_cell_alignment,
                    name="word_set_table_cell_alignment",
                    description="[Table Formatting] Set horizontal and vertical alignment for a table cell"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.set_table_column_width,
                    name="word_set_table_column_width",
                    description="[Table Formatting] Set the width of a table column in points or percentage"
                ),
            ]
            
            # Category 7: Comment Extraction
            comment_tools = [
                StructuredTool.from_function(
                    coroutine=word_tools.get_all_comments,
                    name="word_get_all_comments",
                    description="[Comment Extraction] Get all comments from the document"
                ),
                StructuredTool.from_function(
                    coroutine=word_tools.get_comments_by_author,
                    name="word_get_comments_by_author",
                    description="[Comment Extraction] Get comments from the document filtered by author name"
                ),
            ]
            
            # Combine all tools
            all_tools = (
                doc_creation_tools +
                content_tools +
                manipulation_tools +
                extraction_tools +
                text_format_tools +
                table_format_tools +
                comment_tools
            )
            
            logger.info(f"创建了 {len(all_tools)} 个 Word 工具 (直接调用)")
            return all_tools

        except Exception as e:
            logger.error(f"创建 Word 工具失败: {e}", exc_info=True)
            return []

    @staticmethod
    def create_standard_tools() -> List[BaseTool]:
        """
        创建 LangChain 标准工具

        Returns:
            LangChain 标准工具列表
        """
        try:
            tools = [
                DuckDuckGoSearchRun(
                    name="web_search",
                    description="在网络上搜索学术资料、背景信息和最新研究"
                )
            ]

            logger.info(f"创建了 {len(tools)} 个标准工具")
            return tools

        except Exception as e:
            logger.error(f"创建标准工具失败: {e}")
            return []

    @staticmethod
    def create_base_tools(workspace_dir: str, stream_manager=None) -> List[BaseTool]:
        """
        创建基础工具（不包含writemd等Markdown工具）
        用于Word模式，只包含附件读取、搜索、目录树等通用工具

        Args:
            workspace_dir: 工作空间目录
            stream_manager: 流式输出管理器

        Returns:
            基础工具列表
        """
        try:
            os.environ["WORKSPACE_DIR"] = workspace_dir
            file_tools_instance = FileTools(stream_manager)

            base_tools = [
                StructuredTool.from_function(
                    func=file_tools_instance.tree,
                    name="tree",
                    description="显示工作空间的目录树结构，帮助了解文件组织",
                    args_schema={
                        "directory": {"type": "string", "description": "要显示的目录路径（可选，默认显示整个工作空间）"}
                    }
                ),
                StructuredTool.from_function(
                    func=file_tools_instance.list_attachments,
                    name="list_attachments",
                    description="列出工作空间中所有附件文件，包括文件类型、大小等信息"
                )
            ]

            # 添加标准工具（搜索）
            standard_tools = LangChainToolFactory.create_standard_tools()
            base_tools.extend(standard_tools)

            logger.info(f"创建了 {len(base_tools)} 个基础工具（不含writemd）")
            return base_tools

        except Exception as e:
            logger.error(f"创建基础工具失败: {e}")
            return []

    @staticmethod
    def create_all_tools(workspace_dir: str, stream_manager=None,
                        include_template: bool = False) -> List[BaseTool]:
        """
        创建论文写作需要的所有工具（包括writemd等Markdown工具）

        Args:
            workspace_dir: 工作空间目录
            stream_manager: 流式输出管理器
            include_template: 是否包含模板工具

        Returns:
            所有工具的列表
        """
        try:
            all_tools = []

            # 添加文件工具（包含writemd）
            file_tools = LangChainToolFactory.create_file_tools(workspace_dir, stream_manager)
            all_tools.extend(file_tools)

            # 添加标准工具
            standard_tools = LangChainToolFactory.create_standard_tools()
            all_tools.extend(standard_tools)

            # 可选：添加模板工具
            if include_template:
                template_tools = LangChainToolFactory.create_template_tools(workspace_dir, stream_manager)
                all_tools.extend(template_tools)

            logger.info(f"总共创建了 {len(all_tools)} 个工具")
            return all_tools

        except Exception as e:
            logger.error(f"创建所有工具失败: {e}")
            return []

    @staticmethod
    def get_tool_descriptions(tools: List[BaseTool]) -> Dict[str, str]:
        """
        获取工具描述字典

        Args:
            tools: 工具列表

        Returns:
            工具名称到描述的映射
        """
        descriptions = {}
        for tool in tools:
            descriptions[tool.name] = tool.description
        return descriptions

    @staticmethod
    def create_custom_tool(func, name: str, description: str,
                          args_schema: Optional[Dict[str, Any]] = None) -> StructuredTool:
        """
        创建自定义 LangChain 工具的便捷方法

        Args:
            func: 工具函数
            name: 工具名称
            description: 工具描述
            args_schema: 参数模式（可选）

        Returns:
            LangChain 工具
        """
        if args_schema:
            return StructuredTool.from_function(
                func=func,
                name=name,
                description=description,
                args_schema=args_schema
            )
        else:
            return StructuredTool.from_function(
                func=func,
                name=name,
                description=description
            )


# 便捷函数
def create_paper_writing_tools(workspace_dir: str, stream_manager=None) -> List[BaseTool]:
    """创建论文写作工具集的便捷函数"""
    return LangChainToolFactory.create_all_tools(workspace_dir, stream_manager, include_template=True)


def create_data_analysis_tools(workspace_dir: str, stream_manager=None) -> List[BaseTool]:
    """创建数据分析工具集的便捷函数"""
    return LangChainToolFactory.create_file_tools(workspace_dir, stream_manager)


def create_research_tools(workspace_dir: str, stream_manager=None) -> List[BaseTool]:
    """创建研究工具集的便捷函数"""
    return LangChainToolFactory.create_standard_tools() + \
           LangChainToolFactory.create_file_tools(workspace_dir, stream_manager)
