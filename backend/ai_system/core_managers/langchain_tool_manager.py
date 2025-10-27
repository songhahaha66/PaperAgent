"""
LangChain 工具管理器
使用 LangChain 和 smolagents 的标准工具，大幅减少自定义代码
"""

import os
import logging
from typing import Dict, Any, Optional, List
from langchain_core.tools import StructuredTool, Tool
from langchain_core.tools.base import BaseTool
from langchain_community.tools import DuckDuckGoSearchRun
from smolagents import CodeAgent, PythonInterpreterTool, WebSearchTool

logger = logging.getLogger(__name__)


class LangChainToolManager:
    """基于 LangChain 和 smolagents 的工具管理器"""

    def __init__(self, workspace_dir: str, stream_manager=None):
        """
        初始化 LangChain 工具管理器

        Args:
            workspace_dir: 工作空间目录路径
            stream_manager: 流式输出管理器
        """
        self.workspace_dir = workspace_dir
        self.stream_manager = stream_manager
        self.langchain_tools: List[BaseTool] = []

        # 初始化工具
        self._initialize_tools()

        logger.info(f"LangChainToolManager 初始化完成，工作空间: {workspace_dir}")

    def _initialize_tools(self):
        """初始化标准工具，使用最少代码"""
        try:
            # 1. 网络搜索工具 - 使用 DuckDuckGo
            search_tool = DuckDuckGoSearchRun()
            search_tool.name = "web_search"
            search_tool.description = "在网络上搜索学术资料和背景信息"
            self.langchain_tools.append(search_tool)

            # 2. 文件写入工具 - 简单的文件保存
            write_file_tool = Tool(
                name="save_file",
                description="保存文本内容到文件，用于保存论文草稿",
                func=self._save_file
            )
            self.langchain_tools.append(write_file_tool)

            # 3. 文件读取工具 - 简单的文件读取
            read_file_tool = Tool(
                name="load_file",
                description="读取文本文件内容，用于查看保存的论文草稿",
                func=self._load_file
            )
            self.langchain_tools.append(read_file_tool)

            logger.info(f"工具初始化完成: {[tool.name for tool in self.langchain_tools]}")

        except Exception as e:
            logger.error(f"工具初始化失败: {e}")
            raise

    def _save_file(self, input_str: str) -> str:
        """保存文件到工作空间

        Args:
            input_str: JSON 格式的字符串

        Returns:
            保存结果信息
        """
        try:
            if input_str.strip().startswith('{') and input_str.strip().endswith('}'):
                # JSON 格式
                import json
                data = json.loads(input_str)
                file_path = data.get('file_path', data.get('path', ''))
                content = data.get('content', '')
            else:
                # 简单格式，将整个输入作为内容，使用默认文件名
                file_path = "output.txt"
                content = input_str

            if not file_path:
                return "保存失败: 未指定文件路径"

            full_path = os.path.join(self.workspace_dir, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)

            return f"文件已保存: {file_path}"

        except Exception as e:
            return f"保存失败: {str(e)}"

    def _load_file(self, file_path: str) -> str:
        """从工作空间读取文件"""
        try:
            full_path = os.path.join(self.workspace_dir, file_path)

            if not os.path.exists(full_path):
                return f"文件不存在: {file_path}"

            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            return content

        except Exception as e:
            return f"读取失败: {str(e)}"

    def get_tools(self) -> List[BaseTool]:
        """获取所有工具"""
        return self.langchain_tools

    def get_tool_names(self) -> List[str]:
        """获取工具名称列表"""
        return [tool.name for tool in self.langchain_tools]

    def add_tool(self, tool: BaseTool):
        """添加工具"""
        self.langchain_tools.append(tool)
        logger.info(f"添加工具: {tool.name}")


class SmolAgentManager:
    """smolagents 管理器，用于 CodeAgent"""

    def __init__(self, workspace_dir: str, model, stream_manager=None):
        """
        初始化 SmolAgent 管理器

        Args:
            workspace_dir: 工作空间目录
            model: LLM 模型实例
            stream_manager: 流式输出管理器
        """
        self.workspace_dir = workspace_dir
        self.model = model
        self.stream_manager = stream_manager

        # 创建工具集
        tools = [
            PythonInterpreterTool(description="执行 Python 代码进行数据分析和计算"),
            WebSearchTool(description="搜索网络信息"),
            self._create_file_tool()
        ]

        # 创建 CodeAgent
        self.agent = CodeAgent(
            tools=tools,
            model=model,
            max_iterations=5,
            verbosity_level=1
        )

        logger.info("SmolAgentManager 初始化完成")

    def _create_file_tool(self):
        """创建文件操作工具"""
        def file_operation(operation: str, path: str, content: str = "") -> str:
            try:
                full_path = os.path.join(self.workspace_dir, path)

                if operation == "write":
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    with open(full_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    return f"文件已写入: {path}"

                elif operation == "read":
                    if not os.path.exists(full_path):
                        return f"文件不存在: {path}"
                    with open(full_path, 'r', encoding='utf-8') as f:
                        return f.read()

                else:
                    return f"不支持的操作: {operation}"

            except Exception as e:
                return f"文件操作失败: {str(e)}"

        # 简化实现，具体格式需要根据 smolagents 版本调整
        return Tool(
            name="file_operations",
            description="文件读写操作，支持读取和写入文本文件",
            func=file_operation
        )

    async def run(self, task: str) -> str:
        """运行 CodeAgent 执行任务"""
        if not self.agent:
            return "CodeAgent 未正确初始化"

        try:
            result = self.agent.run(task)
            return str(result)

        except Exception as e:
            logger.error(f"CodeAgent 执行失败: {e}")
            return f"执行失败: {str(e)}"


# 便捷函数
def create_tool_manager(workspace_dir: str, stream_manager=None) -> LangChainToolManager:
    """创建工具管理器"""
    return LangChainToolManager(workspace_dir, stream_manager)


def create_smolagent_manager(workspace_dir: str, model, stream_manager=None) -> SmolAgentManager:
    """创建 SmolAgent 管理器"""
    return SmolAgentManager(workspace_dir, model, stream_manager)