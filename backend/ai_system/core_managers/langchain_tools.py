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
                ),
                StructuredTool.from_function(
                    func=file_tools.read_attachment,
                    name="read_attachment",
                    description="读取附件文件内容，支持多种格式（PDF、Word、Excel、文本等）",
                    args_schema={
                        "file_path": {"type": "string", "description": "附件文件路径（相对于attachment目录）"}
                    }
                ),
                StructuredTool.from_function(
                    func=file_tools.search_attachments,
                    name="search_attachments",
                    description="在附件文件中搜索关键词",
                    args_schema={
                        "keyword": {"type": "string", "description": "搜索关键词"},
                        "file_type": {"type": "string", "description": "可选的文件类型过滤（如pdf、docx、txt等）"}
                    }
                )
            ]

            logger.info(f"创建了 {len(tools)} 个文件工具")
            return tools

        except Exception as e:
            logger.error(f"创建文件工具失败: {e}")
            return []

    # CodeAgent已移除，直接使用smolagents库

    @staticmethod
    def create_smolagents_tool(workspace_dir: str, stream_manager=None, llm=None) -> Optional[StructuredTool]:
        """
        创建 SmolAgents 代码执行工具

        Args:
            workspace_dir: 工作空间目录
            stream_manager: 流式输出管理器
            llm: LLM实例

        Returns:
            SmolAgents 工具或 None
        """
        try:
            # 直接同步调用SmolAgents
            def sync_smolagents_execute(task_description: str) -> str:
                """同步调用SmolAgents"""
                try:
                    # 直接使用smolagents，不通过异步包装
                    from smolagents import CodeAgent

                    # 检查模型类型
                    if hasattr(llm, 'invoke'):
                        # 这是LangChain LLM，不兼容SmolAgents
                        logger.error("传入了LangChain LLM实例，但SmolAgents需要原生模型")
                        return "错误：传入了不兼容的LangChain LLM实例。请使用SmolAgents原生模型。"

                    # 创建SmolAgents CodeAgent，使用传入的LLM
                    logger.info(f"使用模型创建SmolAgents CodeAgent: {llm}")

                    # 配置额外的授权导入,支持科学计算和数据可视化
                    additional_authorized_imports = [
                        # 基础科学计算库
                        'numpy',
                        'scipy',
                        'pandas',
                        # 可视化库
                        'matplotlib.pyplot',
                        'matplotlib',
                        'seaborn'
                    ]

                    # 配置 matplotlib 使用非交互式后端
                    import os
                    os.environ['MPLBACKEND'] = 'Agg'
                    
                    agent = CodeAgent(
                        tools=[],
                        model=llm,  # 使用传入的LLM实例，应该是SmolAgents原生模型
                        max_steps=10,
                        additional_authorized_imports=additional_authorized_imports
                    )

                    # 直接运行任务
                    result = agent.run(task_description)

                    if hasattr(result, 'content'):
                        return result.content
                    else:
                        return str(result)

                except Exception as e:
                    logger.error(f"SmolAgents 执行失败: {e}")
                    error_msg = str(e)
                    if "'ChatMessage' object is not iterable" in error_msg:
                        return "错误：模型类型不兼容。请确保使用SmolAgents原生模型而不是LangChain LLM。"
                    return f"SmolAgents 执行失败: {error_msg}\n请检查任务描述是否清晰。"

            tool = StructuredTool.from_function(
                func=sync_smolagents_execute,
                name="smolagents_execute",
                description="使用 SmolAgents 执行复杂的代码任务，包括数据分析、图表生成、统计计算等。这是处理复杂代码任务的强大工具。输入应该是一个详细描述要执行的代码任务的字符串。"
            )

            logger.info("创建了 SmolAgents 代码执行工具")
            return tool

        except Exception as e:
            logger.error(f"创建 SmolAgents 工具失败: {e}")
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
    def create_all_tools(workspace_dir: str, stream_manager=None,
                        include_template: bool = False) -> List[BaseTool]:
        """
        创建论文写作需要的所有工具

        Args:
            workspace_dir: 工作空间目录
            stream_manager: 流式输出管理器
            include_template: 是否包含模板工具

        Returns:
            所有工具的列表
        """
        try:
            all_tools = []

            # 添加文件工具
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