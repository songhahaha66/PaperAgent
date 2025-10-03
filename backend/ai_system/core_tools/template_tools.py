"""
AI Agent模板操作工具
为AI提供简单易用的模板操作接口
"""

import os
import logging

logger = logging.getLogger(__name__)


class TemplateAgentTools:
    """AI Agent模板操作工具类，提供简单易用的接口"""

    def __init__(self, workspace_dir: str = None):
        self.workspace_dir = workspace_dir or os.getenv("WORKSPACE_DIR", ".")

    def _read_paper_md(self) -> str:
        """从当前工作目录读取paper.md文件内容"""
        try:
            paper_path = os.path.join(self.workspace_dir, "paper.md")
            if not os.path.exists(paper_path):
                return ""

            with open(paper_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"读取paper.md文件失败: {e}")
            return ""

    def _save_paper_md(self, content: str) -> str:
        """保存内容到paper.md文件"""
        try:
            paper_path = os.path.join(self.workspace_dir, "paper.md")
            with open(paper_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return "✅ 保存成功"
        except Exception as e:
            logger.error(f"保存paper.md文件失败: {e}")
            return f"❌ 保存失败: {str(e)}"

    async def analyze_template(self) -> str:
        """分析paper.md文件的模板结构，为AI提供模板概览"""
        template_content = self._read_paper_md()
        if not template_content:
            return "错误：当前工作目录中没有找到paper.md文件"

        try:
            import re
            headers = []
            lines = template_content.split('\n')

            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if line.startswith('#'):
                    header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
                    if header_match:
                        level = len(header_match.group(1))
                        title = header_match.group(2).strip()
                        headers.append({
                            'level': level,
                            'title': title,
                            'line_number': line_num
                        })

            if not headers:
                return "paper.md文件中没有找到标题结构"

            analysis_lines = []
            analysis_lines.append("📊 模板结构分析")
            analysis_lines.append(f"📝 总共找到 {len(headers)} 个标题")
            analysis_lines.append("")

            # 统计各级标题数量
            level_counts = {}
            for header in headers:
                level = header['level']
                level_counts[level] = level_counts.get(level, 0) + 1

            analysis_lines.append("📈 标题层级分布:")
            for level in sorted(level_counts.keys()):
                count = level_counts[level]
                prefix = "#" * level
                analysis_lines.append(f"   {prefix} 级标题: {count} 个")

            analysis_lines.append("")
            analysis_lines.append("📋 详细标题结构:")

            for header in headers:
                indent = "  " * (header['level'] - 1)
                prefix = "#" * header['level']
                analysis_lines.append(f"{indent}{prefix} {header['title']}")

            return '\n'.join(analysis_lines)

        except Exception as e:
            logger.error(f"分析模板失败: {e}")
            return f"分析模板失败: {str(e)}"

    async def get_section_content(self, section_title: str) -> str:
        """获取paper.md文件中指定章节的内容"""
        template_content = self._read_paper_md()
        if not template_content:
            return "错误：当前工作目录中没有找到paper.md文件"

        try:
            lines = template_content.split('\n')
            section_lines = []
            section_found = False
            section_level = 0
            i = 0

            while i < len(lines):
                line = lines[i]
                stripped_line = line.strip()

                # 检查是否是目标章节
                if (stripped_line.startswith('#') and
                    section_title.lower() in stripped_line.lower()):
                    section_found = True
                    section_level = len(line) - len(line.lstrip('#'))
                    section_lines.append(line)  # 添加章节标题
                    i += 1

                    # 收集章节内容
                    while i < len(lines):
                        next_line = lines[i]
                        next_stripped = next_line.strip()

                        # 如果遇到同级或更高级标题，停止收集
                        if (next_stripped.startswith('#') and
                            len(next_line) - len(next_line.lstrip('#')) <= section_level):
                            break

                        section_lines.append(next_line)
                        i += 1

                    break

                i += 1

            if section_found:
                return '\n'.join(section_lines)
            else:
                return f"未找到章节: {section_title}"

        except Exception as e:
            logger.error(f"获取章节内容失败: {e}")
            return f"获取章节内容失败: {str(e)}"

    async def update_section_content(self, section_title: str, new_content: str) -> str:
        """更新paper.md文件中指定章节的内容"""
        template_content = self._read_paper_md()
        if not template_content:
            return "错误：当前工作目录中没有找到paper.md文件"

        try:
            lines = template_content.split('\n')
            result_lines = []
            section_found = False
            section_level = 0
            i = 0

            while i < len(lines):
                line = lines[i]
                stripped_line = line.strip()

                # 检查是否是目标章节
                if (stripped_line.startswith('#') and
                    section_title.lower() in stripped_line.lower()):
                    section_found = True
                    section_level = len(line) - len(line.lstrip('#'))
                    result_lines.append(line)  # 添加章节标题
                    i += 1

                    # 跳过原章节内容
                    while i < len(lines):
                        next_line = lines[i]
                        next_stripped = next_line.strip()

                        # 如果遇到同级或更高级标题，停止跳过
                        if (next_stripped.startswith('#') and
                            len(next_line) - len(next_line.lstrip('#')) <= section_level):
                            break

                        i += 1

                    # 添加新内容
                    if new_content.strip():
                        result_lines.extend(['', new_content.strip()])

                    break

                result_lines.append(line)
                i += 1

            # 如果没有找到章节，在文件末尾添加
            if not section_found:
                if template_content.strip():
                    result_lines.append('')
                result_lines.append(f"# {section_title}")
                if new_content.strip():
                    result_lines.extend(['', new_content.strip()])

            updated_content = '\n'.join(result_lines)
            save_result = self._save_paper_md(updated_content)

            if "✅" in save_result:
                return f"✅ 章节 '{section_title}' 更新成功"
            else:
                return f"❌ 章节 '{section_title}' 更新失败: {save_result}"

        except Exception as e:
            logger.error(f"更新章节内容失败: {e}")
            return f"❌ 更新章节失败: {str(e)}"

    async def add_section(self, section_title: str, content: str = "") -> str:
        """在paper.md文件末尾添加新章节"""
        template_content = self._read_paper_md()

        try:
            if template_content and template_content.strip():
                new_content = template_content + f"\n\n# {section_title}\n"
            else:
                new_content = f"# {section_title}\n"

            if content.strip():
                new_content += f"\n{content.strip()}\n"

            save_result = self._save_paper_md(new_content)

            if "✅" in save_result:
                return f"✅ 章节 '{section_title}' 添加成功"
            else:
                return f"❌ 章节 '{section_title}' 添加失败: {save_result}"

        except Exception as e:
            logger.error(f"添加章节失败: {e}")
            return f"❌ 添加章节失败: {str(e)}"