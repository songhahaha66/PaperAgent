"""
图片插入工具模块
提供AI将CodeAgent生成的图片插入到markdown文件的功能
"""

import os
import logging
from typing import Optional, List
from pathlib import Path
from datetime import datetime
from .file_tools import FileTools

logger = logging.getLogger(__name__)


class ImageInserter:
    """图片插入工具类，让AI能够将生成的图片插入到markdown文件"""

    def __init__(self, workspace_dir: str, file_tools: FileTools, stream_manager=None):
        """
        初始化图片插入工具

        Args:
            workspace_dir: 工作空间目录路径
            file_tools: 文件工具实例，用于写入markdown文件
            stream_manager: 流管理器，用于发送通知（可选）
        """
        self.workspace_dir = workspace_dir
        self.outputs_dir = os.path.join(workspace_dir, "outputs")
        self.file_tools = file_tools
        self.stream_manager = stream_manager

        # 确保outputs目录存在
        os.makedirs(self.outputs_dir, exist_ok=True)
        logger.info(f"ImageInserter初始化完成，工作空间: {workspace_dir}")

    async def insert_latest_image(self, target_file: str = "paper.md", description: str = "生成的图表",
                                  position: str = "smart") -> str:
        """
        插入最新生成的图片到markdown文件

        Args:
            target_file: 目标markdown文件名
            description: 图片描述文字
            position: 插入位置: "smart"(智能位置), "end"(文件末尾), "beginning"(文件开头)

        Returns:
            操作结果信息
        """
        try:
            # 获取最新图片文件
            latest_image = self._get_latest_image()
            if not latest_image:
                return "outputs目录中没有找到任何图片文件"

            # 构建markdown图片语法
            image_path = f"outputs/{latest_image}"
            markdown_content = f"![{description}]({image_path})"

            # 根据位置选择插入方式
            if position == "smart":
                result = await self._smart_insert(target_file, markdown_content)
            elif position == "beginning":
                result = await self.file_tools.writemd(target_file, markdown_content, mode="insert")
            else:  # 默认插入到文件末尾
                result = await self.file_tools.writemd(target_file, markdown_content, mode="append")

            # 发送通知（如果有stream_manager）
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "image_insert_result",
                        f"成功插入图片: {latest_image} (位置: {position})"
                    )
                except Exception as e:
                    logger.warning(f"发送图片插入通知失败: {e}")

            position_text = {
                "smart": "智能位置",
                "beginning": "文件开头",
                "end": "文件末尾"
            }.get(position, "文件末尾")

            return f"成功插入最新图片 '{latest_image}' 到文件 {target_file} 的{position_text}\n图片描述: {description}\n{result}"

        except Exception as e:
            error_msg = f"插入最新图片失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def _smart_insert(self, target_file: str, markdown_content: str) -> str:
        """
        智能插入图片到合适的位置

        Args:
            target_file: 目标文件名
            markdown_content: 要插入的markdown内容

        Returns:
            插入结果
        """
        try:
            # 构建完整文件路径
            if not target_file.endswith('.md'):
                target_file += '.md'
            file_path = os.path.join(self.workspace_dir, target_file)

            # 如果文件不存在，使用append模式创建
            if not os.path.exists(file_path):
                return await self.file_tools.writemd(target_file, markdown_content, mode="append")

            # 读取文件内容
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 智能插入逻辑：
            # 1. 如果文件是空的，直接插入
            if not content.strip():
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(markdown_content)
                return f"图片已插入到空文件 {target_file}"

            # 2. 寻找合适的插入位置
            lines = content.split('\n')
            best_insert_index = len(lines)  # 默认插入到末尾

            # 按优先级寻找插入位置：
            # - 在结论章节前插入
            # - 在最后一个章节标题后
            # - 在最后一个段落结束后

            # 寻找章节标题（# ## ###）
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i].strip()
                if line.startswith('#'):
                    # 找到章节标题，检查是否是结论或参考文献
                    if any(keyword in line.lower() for keyword in ['结论', 'conclusion', '参考文献', 'references', '致谢', 'acknowledgment']):
                        # 在这些章节前插入
                        best_insert_index = i
                        break
                    elif i > len(lines) - 5:  # 如果是倒数5行内的章节，在其后插入
                        best_insert_index = i + 1
                        break

            # 如果没有找到合适的章节位置，寻找段落结尾
            if best_insert_index >= len(lines) - 1:
                for i in range(len(lines) - 1, -1, -1):
                    if lines[i].strip() and not lines[i].strip().startswith('#'):
                        # 找到最后一个非空非标题行
                        best_insert_index = i + 1
                        break

            # 插入内容
            lines.insert(best_insert_index, "")
            lines.insert(best_insert_index + 1, markdown_content)
            lines.insert(best_insert_index + 2, "")

            # 写回文件
            new_content = '\n'.join(lines)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            position_desc = "智能位置"
            if best_insert_index < len(lines) - 3:
                position_desc = f"第{best_insert_index + 1}行后"
            else:
                position_desc = "文件末尾"

            return f"图片已插入到{target_file}的{position_desc}"

        except Exception as e:
            logger.error(f"智能插入失败: {e}")
            # 如果智能插入失败，回退到append模式
            return await self.file_tools.writemd(target_file, markdown_content, mode="append")

    async def list_output_images(self) -> str:
        """
        列出outputs目录中的所有图片文件

        Returns:
            图片文件列表信息
        """
        try:
            if not os.path.exists(self.outputs_dir):
                return "outputs目录不存在"

            # 获取所有图片文件
            image_files = self._get_image_files()

            if not image_files:
                return "outputs目录中没有图片文件"

            # 构建结果信息
            result = f"outputs目录中共有 {len(image_files)} 个图片文件：\n\n"

            for i, (filename, file_info) in enumerate(image_files.items(), 1):
                result += f"{i}. **{filename}**\n"
                result += f"   - 大小: {file_info['size']}\n"
                result += f"   - 修改时间: {file_info['mtime']}\n"
                result += f"   - 相对路径: outputs/{filename}\n\n"

            return result.strip()

        except Exception as e:
            error_msg = f"列出图片文件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def insert_image_by_name(self, image_name: str, target_file: str = "paper.md", description: str = "图表") -> str:
        """
        插入指定名称的图片到markdown文件

        Args:
            image_name: 图片文件名（如：plot_20241015_143022_1.png）
            target_file: 目标markdown文件名
            description: 图片描述文字

        Returns:
            操作结果信息
        """
        try:
            # 验证图片文件是否存在
            image_path = os.path.join(self.outputs_dir, image_name)
            if not os.path.exists(image_path):
                return f"图片文件不存在: {image_name}"

            # 验证文件类型
            if not self._is_image_file(image_name):
                return f"文件不是图片格式: {image_name}"

            # 构建markdown图片语法
            markdown_content = f"![{description}](outputs/{image_name})"

            # 使用file_tools写入到文件末尾
            result = await self.file_tools.writemd(target_file, markdown_content, mode="append")

            # 发送通知（如果有stream_manager）
            if self.stream_manager:
                try:
                    await self.stream_manager.send_json_block(
                        "image_insert_result",
                        f"成功插入图片: {image_name}"
                    )
                except Exception as e:
                    logger.warning(f"发送图片插入通知失败: {e}")

            return f"成功插入图片 '{image_name}' 到文件 {target_file}\n图片描述: {description}\n{result}"

        except Exception as e:
            error_msg = f"插入指定图片失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def get_latest_image_info(self) -> str:
        """
        获取最新图片文件的详细信息

        Returns:
            最新图片文件的详细信息
        """
        try:
            latest_image = self._get_latest_image()
            if not latest_image:
                return "outputs目录中没有找到任何图片文件"

            image_path = os.path.join(self.outputs_dir, latest_image)
            file_stat = os.stat(image_path)

            result = f"最新图片文件信息：\n\n"
            result += f"**文件名**: {latest_image}\n"
            result += f"**相对路径**: outputs/{latest_image}\n"
            result += f"**文件大小**: {self._format_file_size(file_stat.st_size)}\n"
            result += f"**修改时间**: {datetime.fromtimestamp(file_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')}\n"
            result += f"**完整路径**: {image_path}\n"

            return result

        except Exception as e:
            error_msg = f"获取最新图片信息失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    # 辅助方法
    def _get_latest_image(self) -> Optional[str]:
        """获取最新修改的图片文件名"""
        try:
            image_files = self._get_image_files()
            if not image_files:
                return None

            # 按修改时间排序，返回最新的
            latest_filename = max(image_files.keys(), key=lambda x: image_files[x]['timestamp'])
            return latest_filename

        except Exception as e:
            logger.error(f"获取最新图片失败: {e}")
            return None

    def _get_image_files(self) -> dict:
        """获取所有图片文件信息"""
        image_files = {}

        try:
            for filename in os.listdir(self.outputs_dir):
                file_path = os.path.join(self.outputs_dir, filename)
                if os.path.isfile(file_path) and self._is_image_file(filename):
                    file_stat = os.stat(file_path)
                    image_files[filename] = {
                        'size': self._format_file_size(file_stat.st_size),
                        'mtime': datetime.fromtimestamp(file_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                        'timestamp': file_stat.st_mtime
                    }
        except Exception as e:
            logger.error(f"扫描图片文件失败: {e}")

        return image_files

    def _is_image_file(self, filename: str) -> bool:
        """判断文件是否为图片格式"""
        image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp'}
        return Path(filename).suffix.lower() in image_extensions

    def _format_file_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"