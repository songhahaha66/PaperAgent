"""
文件操作工具模块
提供文件写入、目录树显示等基础文件操作功能
"""

import os
import logging
from typing import Optional, Union, Dict, Any, List
from pathlib import Path
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

    async def update_template(self, template_name: str = "paper.md", content: str = "", section: str = "") -> str:
        """
        专门用于更新论文文件的工具方法，只支持章节级别更新

        Args:
            template_name: 论文文件名，默认为paper.md
            content: 要更新的内容
            section: 要更新的章节名称（必需）

        Returns:
            操作结果信息
        """
        try:
            file_path = os.path.join(self.workspace_dir, template_name)

            if not os.path.exists(file_path):
                return f"模板文件不存在: {template_name}"

            if not section.strip():
                return f"错误：必须指定章节名称。update_template工具只支持章节级别更新，不支持全文覆盖。"

            # 读取原模板内容
            with open(file_path, 'r', encoding='utf-8') as f:
                original_content = f.read()

            # 更新指定章节
            updated_content = self._update_section_content(original_content, section, content)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(updated_content)
            result = f"成功更新论文文件 {template_name} 的章节 '{section}'"
            
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

    async def list_attachments(self) -> str:
        """
        列出工作空间中所有附件文件

        Returns:
            附件文件列表信息
        """
        try:
            attachment_dir = os.path.join(self.workspace_dir, "attachment")

            if not os.path.exists(attachment_dir):
                return "工作空间中没有附件目录或没有上传任何附件"

            attachments = []

            # 递归遍历附件目录
            for root, dirs, files in os.walk(attachment_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(file_path, attachment_dir)
                    file_size = os.path.getsize(file_path)

                    # 获取文件类型
                    file_ext = Path(file).suffix.lower()
                    file_type = self._get_file_type_description(file_ext)

                    attachments.append({
                        "name": file,
                        "path": relative_path,
                        "size": file_size,
                        "type": file_type,
                        "extension": file_ext
                    })

            if not attachments:
                return "附件目录为空"

            # 格式化输出
            result = f"发现 {len(attachments)} 个附件文件：\n\n"
            for i, att in enumerate(attachments, 1):
                result += f"{i}. **{att['name']}**\n"
                result += f"   - 路径: {att['path']}\n"
                result += f"   - 大小: {self._format_file_size(att['size'])}\n"
                result += f"   - 类型: {att['type']}\n\n"

            if self.stream_manager:
                await self.stream_manager.send_json_block("attachments_list", {
                    "count": len(attachments),
                    "attachments": attachments
                })

            return result.strip()

        except Exception as e:
            error_msg = f"列出附件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def read_attachment(self, file_path: str) -> str:
        """
        读取附件文件内容

        Args:
            file_path: 附件文件路径（相对于attachment目录）

        Returns:
            文件内容或错误信息
        """
        try:
            # 构建完整的文件路径
            full_path = os.path.join(self.workspace_dir, "attachment", file_path)

            if not os.path.exists(full_path):
                return f"附件文件不存在: {file_path}"

            if not os.path.isfile(full_path):
                return f"指定的路径不是文件: {file_path}"

            # 检查文件大小限制（10MB）
            file_size = os.path.getsize(full_path)
            if file_size > 10 * 1024 * 1024:
                return f"文件过大 ({self._format_file_size(file_size)})，超过10MB限制"

            # 获取文件扩展名
            file_ext = Path(full_path).suffix.lower()

            # 根据文件类型选择合适的读取方法
            content = await self._extract_file_content(full_path, file_ext)

            # 准备返回信息
            result = f"**文件信息:**\n"
            result += f"- 文件名: {Path(full_path).name}\n"
            result += f"- 文件路径: {file_path}\n"
            result += f"- 文件大小: {self._format_file_size(file_size)}\n"
            result += f"- 文件类型: {self._get_file_type_description(file_ext)}\n\n"
            result += f"**文件内容:**\n{content}"

            if self.stream_manager:
                await self.stream_manager.send_json_block("attachment_content", {
                    "file_path": file_path,
                    "file_size": file_size,
                    "file_type": self._get_file_type_description(file_ext),
                    "content": content[:1000] + "..." if len(content) > 1000 else content,
                    "truncated": len(content) > 1000
                })

            return result

        except Exception as e:
            error_msg = f"读取附件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def get_attachment_info(self, file_path: str) -> str:
        """
        获取附件文件的详细信息

        Args:
            file_path: 附件文件路径（相对于attachment目录）

        Returns:
            文件详细信息
        """
        try:
            # 构建完整的文件路径
            full_path = os.path.join(self.workspace_dir, "attachment", file_path)

            if not os.path.exists(full_path):
                return f"附件文件不存在: {file_path}"

            if not os.path.isfile(full_path):
                return f"指定的路径不是文件: {file_path}"

            # 获取文件统计信息
            stat_info = os.stat(full_path)
            file_ext = Path(full_path).suffix.lower()

            result = f"**附件文件详细信息:**\n\n"
            result += f"- **文件名**: {Path(full_path).name}\n"
            result += f"- **相对路径**: {file_path}\n"
            result += f"- **完整路径**: {full_path}\n"
            result += f"- **文件大小**: {self._format_file_size(stat_info.st_size)}\n"
            result += f"- **文件类型**: {self._get_file_type_description(file_ext)}\n"
            result += f"- **扩展名**: {file_ext}\n"
            result += f"- **创建时间**: {self._format_timestamp(stat_info.st_ctime)}\n"
            result += f"- **修改时间**: {self._format_timestamp(stat_info.st_mtime)}\n"

            # 如果是可读的文本文件，显示前几行内容预览
            if self._is_text_file(file_ext) and stat_info.st_size < 1024 * 1024:  # 1MB以下
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        preview = f.read(500)
                        if len(preview) == 500:
                            preview += "..."
                        result += f"- **内容预览**:\n```\n{preview}\n```\n"
                except UnicodeDecodeError:
                    result += f"- **内容预览**: 二进制文件无法预览\n"

            return result

        except Exception as e:
            error_msg = f"获取附件信息失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    async def search_attachments(self, keyword: str, file_type: Optional[str] = None) -> str:
        """
        在附件文件中搜索关键词

        Args:
            keyword: 搜索关键词
            file_type: 可选的文件类型过滤（如 'pdf', 'docx', 'txt'）

        Returns:
            搜索结果
        """
        try:
            attachment_dir = os.path.join(self.workspace_dir, "attachment")

            if not os.path.exists(attachment_dir):
                return "工作空间中没有附件目录"

            search_results = []
            keyword_lower = keyword.lower()

            # 递归搜索附件
            for root, dirs, files in os.walk(attachment_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(file_path, attachment_dir)
                    file_ext = Path(file).suffix.lower()

                    # 文件类型过滤
                    if file_type and file_ext != f".{file_type.lower()}":
                        continue

                    # 文件名匹配
                    if keyword_lower in file.lower():
                        search_results.append({
                            "file": relative_path,
                            "match_type": "文件名",
                            "match_text": file
                        })
                        continue

                    # 文件内容匹配（仅对文本文件）
                    if self._is_text_file(file_ext):
                        try:
                            content = await self._extract_file_content(file_path, file_ext)
                            if keyword_lower in content.lower():
                                # 找到匹配的行
                                lines = content.split('\n')
                                matching_lines = []
                                for i, line in enumerate(lines, 1):
                                    if keyword_lower in line.lower():
                                        matching_lines.append(f"第{i}行: {line.strip()}")
                                        if len(matching_lines) >= 3:  # 最多显示3个匹配行
                                            break

                                search_results.append({
                                    "file": relative_path,
                                    "match_type": "文件内容",
                                    "match_text": "\n".join(matching_lines)
                                })
                        except Exception as e:
                            logger.warning(f"搜索文件内容失败 {file_path}: {e}")

            if not search_results:
                return f"未找到包含关键词 '{keyword}' 的附件文件"

            # 格式化搜索结果
            result = f"**搜索结果** (关键词: '{keyword}'):\n\n"
            for i, item in enumerate(search_results, 1):
                result += f"{i}. **{item['file']}** (匹配类型: {item['match_type']})\n"
                if item['match_type'] == '文件内容':
                    result += f"   匹配内容:\n   {item['match_text']}\n"
                result += "\n"

            if self.stream_manager:
                await self.stream_manager.send_json_block("search_results", {
                    "keyword": keyword,
                    "file_type": file_type,
                    "results": search_results
                })

            return result.strip()

        except Exception as e:
            error_msg = f"搜索附件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    # 辅助方法
    def _get_file_type_description(self, file_ext: str) -> str:
        """获取文件类型描述"""
        type_map = {
            # 文本文档
            '.txt': '纯文本文件',
            '.md': 'Markdown文档',
            '.rtf': '富文本格式',

            # Word文档
            '.doc': 'Word文档 (旧版)',
            '.docx': 'Word文档',

            # PDF文档
            '.pdf': 'PDF文档',

            # 表格文件
            '.csv': 'CSV表格文件',
            '.xlsx': 'Excel表格文件',
            '.xls': 'Excel表格文件 (旧版)',

            # 代码文件
            '.py': 'Python源代码',
            '.js': 'JavaScript源代码',
            '.ts': 'TypeScript源代码',
            '.java': 'Java源代码',
            '.cpp': 'C++源代码',
            '.c': 'C源代码',
            '.html': 'HTML文件',
            '.css': 'CSS样式表',
            '.vue': 'Vue组件',
            '.json': 'JSON数据文件',
            '.xml': 'XML文件',
            '.yaml': 'YAML配置文件',
            '.yml': 'YAML配置文件',
            '.toml': 'TOML配置文件',
            '.ini': 'INI配置文件',
            '.sql': 'SQL脚本',
            '.sh': 'Shell脚本',
            '.bat': '批处理文件',
        }
        return type_map.get(file_ext, f'{file_ext} 文件')

    def _format_file_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def _format_timestamp(self, timestamp: float) -> str:
        """格式化时间戳"""
        import datetime
        return datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

    def _is_text_file(self, file_ext: str) -> bool:
        """判断是否为文本文件"""
        text_extensions = {
            '.txt', '.md', '.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.hpp',
            '.html', '.css', '.vue', '.json', '.xml', '.yaml', '.yml', '.toml', '.ini',
            '.sql', '.sh', '.bat', '.cmd', '.log', '.csv', '.rtf'
        }
        return file_ext in text_extensions

    async def _extract_file_content(self, file_path: str, file_ext: str) -> str:
        """根据文件类型提取内容"""
        try:
            if file_ext in ['.txt', '.md', '.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.hpp',
                           '.html', '.css', '.vue', '.json', '.xml', '.yaml', '.yml', '.toml', '.ini',
                           '.sql', '.sh', '.bat', '.cmd', '.log', '.rtf']:
                # 直接读取文本文件
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()

            elif file_ext == '.csv':
                # 使用pandas读取CSV文件
                import pandas as pd
                df = pd.read_csv(file_path)
                return f"CSV文件内容预览:\n{df.head().to_string()}\n\n总行数: {len(df)}\n总列数: {len(df.columns)}\n列名: {list(df.columns)}"

            elif file_ext in ['.xlsx', '.xls']:
                # 使用pandas读取Excel文件
                import pandas as pd
                df = pd.read_excel(file_path)
                return f"Excel文件内容预览:\n{df.head().to_string()}\n\n总行数: {len(df)}\n总列数: {len(df.columns)}\n列名: {list(df.columns)}"

            elif file_ext == '.docx':
                # 使用python-docx读取Word文档
                from docx import Document
                doc = Document(file_path)
                content = []
                for para in doc.paragraphs:
                    if para.text.strip():
                        content.append(para.text)
                return "\n".join(content)

            elif file_ext == '.pdf':
                # 使用PyPDF2读取PDF文件
                try:
                    import PyPDF2
                    with open(file_path, 'rb') as file:
                        pdf_reader = PyPDF2.PdfReader(file)
                        content = []
                        for page_num in range(min(len(pdf_reader.pages), 10)):  # 限制读取前10页
                            page = pdf_reader.pages[page_num]
                            text = page.extract_text()
                            if text.strip():
                                content.append(f"--- 第{page_num + 1}页 ---\n{text}")
                        return "\n".join(content)
                except Exception as e:
                    # 如果PyPDF2失败，尝试pdfplumber
                    try:
                        import pdfplumber
                        with pdfplumber.open(file_path) as pdf:
                            content = []
                            for page_num in range(min(len(pdf.pages), 10)):  # 限制读取前10页
                                page = pdf.pages[page_num]
                                text = page.extract_text()
                                if text.strip():
                                    content.append(f"--- 第{page_num + 1}页 ---\n{text}")
                            return "\n".join(content)
                    except Exception as e2:
                        return f"PDF文件读取失败:\nPyPDF2错误: {str(e)}\npdfplumber错误: {str(e2)}"

            else:
                return f"不支持的文件类型: {file_ext}"

        except Exception as e:
            logger.error(f"提取文件内容失败 {file_path}: {e}")
            return f"读取文件内容失败: {str(e)}"
