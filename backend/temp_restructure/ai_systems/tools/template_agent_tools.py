"""
AI Agent模板操作工具
为AI提供简单易用的模板操作接口
"""

import os
import logging
from typing import Dict, Any, Optional, List
from . import template_operations

logger = logging.getLogger(__name__)


class TemplateAgentTools:
    """AI Agent模板操作工具类，提供简单易用的接口"""
    
    def __init__(self, workspace_dir: str = None):
        self.workspace_dir = workspace_dir or os.getenv("WORKSPACE_DIR", ".")
        
    def _read_paper_md(self) -> str:
        """
        从当前工作目录读取paper.md文件内容
        
        Returns:
            文件内容，如果文件不存在则返回空字符串
        """
        try:
            paper_path = os.path.join(self.workspace_dir, "paper.md")
            if not os.path.exists(paper_path):
                return ""
            
            with open(paper_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"读取paper.md文件失败: {e}")
            return ""
    
    async def analyze_template(self) -> str:
        """
        分析paper.md文件的模板结构，为AI提供模板概览
        
        Returns:
            模板分析结果
        """
        template_content = self._read_paper_md()
        if not template_content:
            return "错误：当前工作目录中没有找到paper.md文件"
        return await template_operations.analyze_template(template_content)
    
    async def get_section_content(self, section_title: str) -> str:
        """
        获取paper.md文件中指定章节的内容
        
        Args:
            section_title: 章节标题
            
        Returns:
            章节内容或错误信息
        """
        template_content = self._read_paper_md()
        if not template_content:
            return "错误：当前工作目录中没有找到paper.md文件"
        return await template_operations.get_section_content(template_content, section_title)
    
    async def update_section_content(self, section_title: str, new_content: str, mode: str = 'replace') -> str:
        """
        更新paper.md文件中指定章节的内容
        
        Args:
            section_title: 章节标题
            new_content: 新内容
            mode: 更新模式 ('replace', 'append', 'prepend', 'merge')
            
        Returns:
            更新结果
        """
        template_content = self._read_paper_md()
        if not template_content:
            return "错误：当前工作目录中没有找到paper.md文件"
        
        try:
            # 直接调用底层函数获取更新后的内容
            from . import template_tools
            tools_instance = template_tools.TemplateTools(self.workspace_dir)
            updated_content = tools_instance.update_section_content(
                template_content, section_title, new_content, mode
            )
            
            # 检查内容是否真的更新了
            if updated_content and updated_content != template_content:
                # 保存到文件
                paper_path = os.path.join(self.workspace_dir, "paper.md")
                with open(paper_path, 'w', encoding='utf-8') as f:
                    f.write(updated_content)
                return f"✅ 章节 '{section_title}' 更新成功，已保存到paper.md文件"
            else:
                return f"❌ 更新章节失败：内容未发生变化"
                
        except Exception as e:
            logger.error(f"更新章节内容失败: {e}")
            return f"❌ 更新章节失败: {str(e)}"
    
    async def add_new_section(self, parent_section: str, section_title: str, content: str = '') -> str:
        """
        在paper.md文件中指定父章节下添加新章节
        
        Args:
            parent_section: 父章节标题
            section_title: 新章节标题
            content: 新章节内容
            
        Returns:
            添加结果
        """
        template_content = self._read_paper_md()
        if not template_content:
            return "错误：当前工作目录中没有找到paper.md文件"
        
        try:
            # 直接调用底层函数获取更新后的内容
            from . import template_tools
            tools_instance = template_tools.TemplateTools(self.workspace_dir)
            updated_content = tools_instance.add_new_section(
                template_content, parent_section, section_title, content
            )
            
            # 检查内容是否真的更新了
            if updated_content and updated_content != template_content:
                # 保存到文件
                paper_path = os.path.join(self.workspace_dir, "paper.md")
                with open(paper_path, 'w', encoding='utf-8') as f:
                    f.write(updated_content)
                return f"✅ 新章节 '{section_title}' 添加成功，已保存到paper.md文件"
            else:
                return f"❌ 添加章节失败：内容未发生变化"
                
        except Exception as e:
            logger.error(f"添加新章节失败: {e}")
            return f"❌ 添加新章节失败: {str(e)}"
    
    async def remove_section(self, section_title: str) -> str:
        """
        删除paper.md文件中指定章节
        
        Args:
            section_title: 章节标题
            
        Returns:
            删除结果
        """
        template_content = self._read_paper_md()
        if not template_content:
            return "错误：当前工作目录中没有找到paper.md文件"
        
        try:
            # 直接调用底层函数获取更新后的内容
            from . import template_tools
            tools_instance = template_tools.TemplateTools(self.workspace_dir)
            updated_content = tools_instance.remove_section(template_content, section_title)
            
            # 检查内容是否真的更新了
            if updated_content and updated_content != template_content:
                # 保存到文件
                paper_path = os.path.join(self.workspace_dir, "paper.md")
                with open(paper_path, 'w', encoding='utf-8') as f:
                    f.write(updated_content)
                return f"✅ 章节 '{section_title}' 删除成功，已保存到paper.md文件"
            else:
                return f"❌ 删除章节失败：内容未发生变化"
                
        except Exception as e:
            logger.error(f"删除章节失败: {e}")
            return f"❌ 删除章节失败: {str(e)}"
    
    async def reorder_sections(self, section_order: List[str]) -> str:
        """
        重新排序paper.md文件中的章节
        
        Args:
            section_order: 新的章节顺序列表
            
        Returns:
            重排序结果
        """
        template_content = self._read_paper_md()
        if not template_content:
            return "错误：当前工作目录中没有找到paper.md文件"
        
        try:
            # 直接调用底层函数获取更新后的内容
            from . import template_tools
            tools_instance = template_tools.TemplateTools(self.workspace_dir)
            updated_content = tools_instance.reorder_sections(template_content, section_order)
            
            # 检查内容是否真的更新了
            if updated_content and updated_content != template_content:
                # 保存到文件
                paper_path = os.path.join(self.workspace_dir, "paper.md")
                with open(paper_path, 'w', encoding='utf-8') as f:
                    f.write(updated_content)
                return f"✅ 章节重排序成功，已保存到paper.md文件"
            else:
                return f"❌ 重排序失败：内容未发生变化"
                
        except Exception as e:
            logger.error(f"重排序章节失败: {e}")
            return f"❌ 重排序失败: {str(e)}"
    
    async def format_template(self, format_options: Dict[str, Any] = None) -> str:
        """
        格式化paper.md文件内容
        
        Args:
            format_options: 格式化选项
            
        Returns:
            格式化结果
        """
        template_content = self._read_paper_md()
        if not template_content:
            return "错误：当前工作目录中没有找到paper.md文件"
        
        try:
            # 直接调用底层函数获取更新后的内容
            from . import template_tools
            tools_instance = template_tools.TemplateTools(self.workspace_dir)
            formatted_content = tools_instance.format_template_content(template_content, format_options)
            
            # 检查内容是否真的更新了
            if formatted_content and formatted_content != template_content:
                # 保存到文件
                paper_path = os.path.join(self.workspace_dir, "paper.md")
                with open(paper_path, 'w', encoding='utf-8') as f:
                    f.write(formatted_content)
                return f"✅ 模板格式化完成，已保存到paper.md文件"
            else:
                return f"✅ 模板格式化完成：内容无需格式化"
                
        except Exception as e:
            logger.error(f"格式化模板失败: {e}")
            return f"❌ 格式化失败: {str(e)}"
    
    async def get_template_help(self) -> str:
        """
        获取模板操作帮助信息
        
        Returns:
            帮助信息
        """
        return await template_operations.get_template_help()
    
    async def extract_headers_from_content(self) -> List[Dict[str, Any]]:
        """
        从paper.md文件中提取所有标题信息
        
        Returns:
            标题信息列表
        """
        template_content = self._read_paper_md()
        if not template_content:
            return []
        
        try:
            import re
            headers = []
            lines = template_content.split('\n')
            
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if line.startswith('#'):
                    # 匹配标题行
                    header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
                    if header_match:
                        level = len(header_match.group(1))
                        title = header_match.group(2).strip()
                        
                        headers.append({
                            'level': level,
                            'title': title,
                            'line_number': line_num,
                            'markdown': line
                        })
            
            return headers
            
        except Exception as e:
            logger.error(f"提取标题失败: {e}")
            return []
    
    async def get_content_structure_summary(self) -> str:
        """
        获取paper.md文件的内容结构摘要
        
        Returns:
            结构摘要
        """
        template_content = self._read_paper_md()
        if not template_content:
            return "错误：当前工作目录中没有找到paper.md文件"
        
        try:
            headers = await self.extract_headers_from_content()
            
            if not headers:
                return "paper.md文件中没有找到标题结构"
            
            summary_lines = []
            summary_lines.append(f"📋 内容结构摘要 (共 {len(headers)} 个标题)")
            summary_lines.append("")
            
            for header in headers:
                indent = "  " * (header['level'] - 1)
                summary_lines.append(f"{indent}{'#' * header['level']} {header['title']}")
            
            return '\n'.join(summary_lines)
            
        except Exception as e:
            logger.error(f"生成结构摘要失败: {e}")
            return f"生成结构摘要失败: {str(e)}"


# 不再创建全局实例，由MainAgent在初始化时创建
