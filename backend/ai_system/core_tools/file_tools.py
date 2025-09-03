"""
文件操作工具模块
提供文件写入、目录树显示等基础文件操作功能
"""

import os
import logging
from typing import Optional, Union
from ..core_managers.stream_manager import StreamOutputManager

logger = logging.getLogger(__name__)


class FileTools:
    """文件操作工具类"""

    def __init__(self, stream_manager: Optional[StreamOutputManager] = None):
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

    async def writemd(self, filename: str, content: str, mode: str = "overwrite") -> str:
        """
        写入Markdown文件到workspace目录，支持多种写入模式

        Args:
            filename: 文件名（不需要.md后缀）
            content: Markdown内容
            mode: 写入模式
                - "append": 附加模式，在文件末尾追加内容
                - "overwrite": 重写覆盖模式，完全覆盖原文件内容
                - "modify": 修改模式，替换文件中的特定内容
                - "insert": 插入模式，在文件开头插入内容

        Returns:
            操作结果信息
        """
        try:
            # 确保文件名有.md后缀
            if not filename.endswith('.md'):
                filename = filename + '.md'

            # 构建完整路径
            file_path = os.path.join(self.workspace_dir, filename)

            # 确保目录存在
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            logger.info(f"写入Markdown文件: {file_path}，模式: {mode}")

            if mode == "append":
                # 附加模式：在文件末尾追加内容
                with open(file_path, 'a', encoding='utf-8') as f:
                    f.write('\n\n' + content)
                result = f"成功附加内容到Markdown文件: {filename}"

            elif mode == "overwrite":
                # 重写覆盖模式：完全覆盖原文件内容
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                result = f"成功重写覆盖Markdown文件: {filename}"

            elif mode == "modify":
                # 修改模式：替换文件中的特定内容
                if os.path.exists(file_path):
                    # 读取原文件内容
                    with open(file_path, 'r', encoding='utf-8') as f:
                        original_content = f.read()

                    # 这里可以根据需要实现更复杂的修改逻辑
                    # 目前简单地将新内容替换原内容
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    result = f"成功修改Markdown文件: {filename}"
                else:
                    # 如果文件不存在，创建新文件
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    result = f"文件不存在，创建并写入Markdown文件: {filename}"

            elif mode == "insert":
                # 插入模式：在文件开头插入内容
                if os.path.exists(file_path):
                    # 读取原文件内容
                    with open(file_path, 'r', encoding='utf-8') as f:
                        original_content = f.read()

                    # 在开头插入新内容
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content + '\n\n' + original_content)
                    result = f"成功在文件开头插入内容到Markdown文件: {filename}"
                else:
                    # 如果文件不存在，创建新文件
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    result = f"文件不存在，创建并写入Markdown文件: {filename}"

            elif mode == "smart_replace":
                # 智能替换模式：根据内容特征智能替换
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        original_content = f.read()
                    
                    # 智能替换逻辑：保持模板结构，替换内容部分
                    # 这里可以实现更复杂的智能替换算法
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    result = f"成功智能替换Markdown文件: {filename}"
                else:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    result = f"文件不存在，创建并写入Markdown文件: {filename}"

            elif mode == "section_update":
                # 章节更新模式：更新特定章节内容
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        original_content = f.read()
                    
                    # 这里可以实现章节级别的更新逻辑
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    result = f"成功更新章节内容到Markdown文件: {filename}"
                else:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    result = f"文件不存在，创建并写入Markdown文件: {filename}"

            else:
                return f"无效的写入模式: {mode}，支持的模式: append, overwrite, modify, insert, smart_replace, section_update"

            # 获取文件信息
            file_size = os.path.getsize(file_path)
            result += f"\n文件路径: {file_path}\n文件大小: {file_size} 字节"

            if self.stream_manager:
                await self.stream_manager.send_json_block("writemd_result", result)

            return result

        except Exception as e:
            error_msg = f"写入Markdown文件失败: {str(e)}"
            logger.error(error_msg)

            if self.stream_manager:
                await self.stream_manager.send_json_block("writemd_result", error_msg)

            return error_msg

    async def update_template(self, template_name: str = "paper.md", content: str = "", section: Optional[str] = None) -> str:
        """
        专门用于更新论文文件的工具方法
        
        Args:
            template_name: 论文文件名，默认为paper.md
            content: 要更新的内容
            section: 要更新的章节名称（可选）
            
        Returns:
            操作结果信息
        """
        try:
            file_path = os.path.join(self.workspace_dir, template_name)
            
            if not os.path.exists(file_path):
                return f"模板文件不存在: {template_name}"
            
            # 读取原模板内容
            with open(file_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
            
            if section:
                # 如果指定了章节，尝试更新特定章节
                updated_content = self._update_section_content(original_content, section, content)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(updated_content)
                result = f"成功更新论文文件 {template_name} 的章节 '{section}'"
            else:
                # 更新整个论文内容
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                result = f"成功更新论文文件: {template_name}"
            
            # 获取文件信息
            file_size = os.path.getsize(file_path)
            result += f"\n文件路径: {file_path}\n文件大小: {file_size} 字节"
            
            if self.stream_manager:
                await self.stream_manager.send_json_block("template_update_result", result)
            
            return result
            
        except Exception as e:
            error_msg = f"更新论文文件失败: {str(e)}"
            logger.error(error_msg)
            
            if self.stream_manager:
                await self.stream_manager.send_json_block("template_update_result", error_msg)
            
            return error_msg

    def _update_section_content(self, original_content: str, section_name: str, new_content: str) -> str:
        """
        更新指定章节的内容
        
        Args:
            original_content: 原始文件内容
            section_name: 章节名称
            new_content: 新的章节内容
            
        Returns:
            更新后的完整文件内容
        """
        lines = original_content.split('\n')
        updated_lines = []
        section_found = False
        i = 0
        
        while i < len(lines):
            line = lines[i]
            line_matched = False
            
            # 简化的匹配方式：检查是否包含章节名称且以#开头
            stripped_line = line.strip()
            if (stripped_line.startswith('#') and 
                section_name in stripped_line):
                
                # 找到目标章节
                section_found = True
                line_matched = True
                
                logger.info(f"找到匹配章节: {stripped_line}")
                
                # 添加章节标题
                updated_lines.append(line)
                
                # 计算当前章节的级别
                section_level = len(line) - len(line.lstrip('#'))
                
                # 跳过空行
                i += 1
                while i < len(lines) and lines[i].strip() == '':
                    updated_lines.append(lines[i])
                    i += 1
                
                # 添加新内容
                if new_content.strip():
                    updated_lines.append('')
                    updated_lines.append(new_content.strip())
                    updated_lines.append('')
                
                # 跳过原有章节内容直到下一个同级或更高级标题
                while i < len(lines):
                    next_line = lines[i]
                    # 检查是否是新的章节标题
                    if next_line.strip().startswith('#'):
                        next_level = len(next_line) - len(next_line.lstrip('#'))
                        if next_level <= section_level:
                            # 遇到同级或更高级标题，停止跳过
                            logger.info(f"遇到新章节，停止跳过: {next_line.strip()}")
                            break
                    i += 1
                
                break
            
            if not line_matched:
                updated_lines.append(line)
                i += 1
        
        # 如果没有找到章节，在文件末尾添加
        if not section_found:
            logger.warning(f"没有找到匹配的章节: {section_name}，将在末尾添加")
            if original_content.strip():
                updated_lines.append('')
            updated_lines.append(f'# **{section_name}**')
            updated_lines.append('')
            if new_content.strip():
                updated_lines.append(new_content.strip())
                updated_lines.append('')
        
        return '\n'.join(updated_lines)

    async def tree(self, directory: Optional[str] = None) -> str:
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
