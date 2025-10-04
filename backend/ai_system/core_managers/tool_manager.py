"""
统一工具管理器
集中管理所有工具类，提供统一的初始化、获取和注册接口
"""

import os
import logging
from typing import Dict, Any, Optional
from ..core_tools.file_tools import FileTools
from ..core_tools.template_tools import TemplateAgentTools
from ..core_tools.code_executor import CodeExecutor

logger = logging.getLogger(__name__)


class ToolRegistry:
    """统一的工具注册表和管理器"""

    def __init__(self, workspace_dir: str, stream_manager=None):
        """
        初始化工具注册表

        Args:
            workspace_dir: 工作空间目录路径
            stream_manager: 流式输出管理器
        """
        self.workspace_dir = workspace_dir
        self.stream_manager = stream_manager
        self._tools: Dict[str, Any] = {}
        self._initialize_tools()
        logger.info(f"ToolRegistry初始化完成，工作空间: {workspace_dir}")

    def _initialize_tools(self):
        """初始化所有可用的工具"""
        try:
            # 初始化文件工具
            self._tools['file'] = FileTools(self.stream_manager)

            # 初始化代码执行器
            self._tools['code_executor'] = CodeExecutor(self.stream_manager, self.workspace_dir)

            # 初始化模板工具（如果需要）
            if self.workspace_dir:
                self._tools['template'] = TemplateAgentTools(self.workspace_dir)

            logger.info(f"工具初始化完成，已注册工具: {list(self._tools.keys())}")

        except Exception as e:
            logger.error(f"工具初始化失败: {e}")
            raise

    def get_tool(self, tool_name: str):
        """
        获取指定工具实例

        Args:
            tool_name: 工具名称

        Returns:
            工具实例

        Raises:
            ValueError: 工具不存在时抛出
        """
        if tool_name not in self._tools:
            available_tools = list(self._tools.keys())
            raise ValueError(f"工具 '{tool_name}' 不存在。可用工具: {available_tools}")

        tool = self._tools[tool_name]
        logger.debug(f"获取工具: {tool_name} -> {type(tool).__name__}")
        return tool

    def register_tool(self, name: str, tool_instance: Any):
        """
        注册自定义工具

        Args:
            name: 工具名称
            tool_instance: 工具实例
        """
        if name in self._tools:
            logger.warning(f"工具 '{name}' 已存在，将被覆盖")

        self._tools[name] = tool_instance
        logger.info(f"注册自定义工具: {name} -> {type(tool_instance).__name__}")

    def list_tools(self) -> Dict[str, str]:
        """
        列出所有已注册的工具

        Returns:
            工具名称到工具类名的映射
        """
        return {name: type(tool).__name__ for name, tool in self._tools.items()}

    def has_tool(self, tool_name: str) -> bool:
        """
        检查指定工具是否存在

        Args:
            tool_name: 工具名称

        Returns:
            工具是否存在
        """
        return tool_name in self._tools

    def get_workspace_dir(self) -> str:
        """获取工作空间目录路径"""
        return self.workspace_dir

    def get_stream_manager(self):
        """获取流式输出管理器"""
        return self.stream_manager


class ToolManager:
    """工具管理器的便捷接口"""

    def __init__(self, workspace_dir: str, stream_manager=None):
        self.registry = ToolRegistry(workspace_dir, stream_manager)

    def get(self, tool_name: str):
        """获取工具的便捷方法"""
        return self.registry.get_tool(tool_name)

    def file(self):
        """获取文件工具"""
        return self.registry.get_tool('file')

    def template(self):
        """获取模板工具"""
        return self.registry.get_tool('template')

    def code_executor(self):
        """获取代码执行器"""
        return self.registry.get_tool('code_executor')

    def register(self, name: str, tool_instance: Any):
        """注册工具的便捷方法"""
        self.registry.register_tool(name, tool_instance)

    def list_all(self) -> Dict[str, str]:
        """列出所有工具"""
        return self.registry.list_tools()