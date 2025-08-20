"""
文件操作工具模块
提供文件写入、目录树显示等基础文件操作功能
"""

import os
import logging
from typing import Optional
from ..core.stream_manager import StreamOutputManager

logger = logging.getLogger(__name__)


class FileTools:
    """文件操作工具类"""

    def __init__(self, stream_manager: StreamOutputManager = None):
        # 获取工作空间目录
        workspace_dir = os.getenv("WORKSPACE_DIR")
        if not workspace_dir:
            # 必须设置工作空间目录，不能使用默认路径
            raise ValueError("必须设置WORKSPACE_DIR环境变量，指定具体的工作空间目录（包含work_id）")

        self.workspace_dir = workspace_dir
        self.stream_manager = stream_manager

        # 确保工作空间目录存在
        os.makedirs(self.workspace_dir, exist_ok=True)
        logger.info(f"FileTools初始化完成，workspace目录: {self.workspace_dir}")

    async def writemd(self, filename: str, content: str) -> str:
        """将内容写入Markdown文件到workspace目录"""
        try:
            # 确保文件名有.md后缀
            if not filename.endswith('.md'):
                filename = filename + '.md'

            # 构建完整路径
            file_path = os.path.join(self.workspace_dir, filename)

            # 确保目录存在
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            # 写入文件
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)

            result = f"Markdown文件 '{filename}' 已成功写入到工作空间目录。\n文件路径: {file_path}\n文件大小: {len(content)} 字符"

            if self.stream_manager:
                await self.stream_manager.send_json_block("writemd_result", result)

            return result

        except Exception as e:
            error_msg = f"写入Markdown文件失败: {str(e)}"
            logger.error(error_msg)

            if self.stream_manager:
                await self.stream_manager.send_json_block("writemd_result", error_msg)

            return error_msg

    async def tree(self, directory: str = None) -> str:
        """显示目录树结构"""
        try:
            if directory is None:
                directory = self.workspace_dir

            if not os.path.exists(directory):
                return f"目录不存在: {directory}"

            def _tree_helper(path: str, prefix: str = "", is_last: bool = True) -> str:
                result = []
                items = os.listdir(path)
                items.sort()

                for i, item in enumerate(items):
                    item_path = os.path.join(path, item)
                    is_last_item = i == len(items) - 1

                    if os.path.isdir(item_path):
                        result.append(
                            f"{prefix}{'└── ' if is_last_item else '├── '}{item}/")
                        new_prefix = prefix + \
                            ('    ' if is_last_item else '│   ')
                        result.append(_tree_helper(
                            item_path, new_prefix, is_last_item))
                    else:
                        result.append(
                            f"{prefix}{'└── ' if is_last_item else '├── '}{item}")

                return '\n'.join(result)

            tree_result = f"{os.path.basename(directory)}/\n" + \
                _tree_helper(directory)

            if self.stream_manager:
                await self.stream_manager.send_json_block("tree_result", tree_result)

            return tree_result

        except Exception as e:
            error_msg = f"生成目录树失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    def get_workspace_dir(self) -> str:
        """获取工作空间目录"""
        return self.workspace_dir

    def file_exists(self, filename: str) -> bool:
        """检查文件是否存在"""
        filepath = os.path.join(self.workspace_dir, filename)
        return os.path.exists(filepath)

    def read_file(self, filename: str) -> Optional[str]:
        """读取文件内容"""
        filepath = os.path.join(self.workspace_dir, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"读取文件失败: {e}")
            return None

    def list_files(self, directory: Optional[str] = None) -> list:
        """列出目录中的文件"""
        if directory is None:
            directory = self.workspace_dir

        if not os.path.exists(directory):
            return []

        try:
            return os.listdir(directory)
        except Exception as e:
            logger.error(f"列出目录失败: {e}")
            return []
