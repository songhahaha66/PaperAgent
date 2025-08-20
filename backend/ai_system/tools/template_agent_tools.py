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
        
    async def analyze_template(self, template_content: str) -> str:
        """
        分析模板结构，为AI提供模板概览
        
        Args:
            template_content: 模板内容
            
        Returns:
            模板分析结果
        """
        return await template_operations.analyze_template(template_content)
    
    async def get_section_content(self, template_content: str, section_title: str) -> str:
        """
        获取指定章节的内容
        
        Args:
            template_content: 模板内容
            section_title: 章节标题
            
        Returns:
            章节内容或错误信息
        """
        return await template_operations.get_section_content(template_content, section_title)
    
    async def update_section_content(self, template_content: str, section_title: str, new_content: str, mode: str = 'replace') -> str:
        """
        更新指定章节的内容
        
        Args:
            template_content: 模板内容
            section_title: 章节标题
            new_content: 新内容
            mode: 更新模式 ('replace', 'append', 'prepend', 'merge')
            
        Returns:
            更新结果
        """
        return await template_operations.update_section_content(template_content, section_title, new_content, mode)
    
    async def add_new_section(self, template_content: str, parent_section: str, section_title: str, content: str = '') -> str:
        """
        添加新章节
        
        Args:
            template_content: 模板内容
            parent_section: 父章节标题
            section_title: 新章节标题
            content: 新章节内容
            
        Returns:
            添加结果
        """
        return await template_operations.add_new_section(template_content, parent_section, section_title, content)
    
    async def remove_section(self, template_content: str, section_title: str) -> str:
        """
        删除指定章节
        
        Args:
            template_content: 模板内容
            section_title: 章节标题
            
        Returns:
            删除结果
        """
        return await template_operations.remove_section(template_content, section_title)
    
    async def reorder_sections(self, template_content: str, section_order: List[str]) -> str:
        """
        重新排序章节
        
        Args:
            template_content: 模板内容
            section_order: 新的章节顺序列表
            
        Returns:
            重排序结果
        """
        return await template_operations.reorder_sections(template_content, section_order)
    
    async def format_template(self, template_content: str, format_options: Dict[str, Any] = None) -> str:
        """
        格式化模板内容
        
        Args:
            template_content: 模板内容
            format_options: 格式化选项
            
        Returns:
            格式化结果
        """
        return await template_operations.format_template(template_content, format_options)
    
    async def get_template_help(self) -> str:
        """
        获取模板操作帮助信息
        
        Returns:
            帮助信息
        """
        return await template_operations.get_template_help()
    
    def extract_headers_from_content(self, content: str) -> List[Dict[str, Any]]:
        """
        从内容中提取所有标题信息
        
        Args:
            content: 内容字符串
            
        Returns:
            标题信息列表
        """
        try:
            import re
            headers = []
            lines = content.split('\n')
            
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
    
    def get_content_structure_summary(self, content: str) -> str:
        """
        获取内容结构摘要
        
        Args:
            content: 内容字符串
            
        Returns:
            结构摘要
        """
        try:
            headers = self.extract_headers_from_content(content)
            
            if not headers:
                return "内容中没有找到标题结构"
            
            summary_lines = []
            summary_lines.append(f"📋 内容结构摘要 (共 {len(headers)} 个标题)")
            summary_lines.append("")
            
            for header in headers:
                indent = "  " * (header['level'] - 1)
                summary_lines.append(f"{indent}{'#' * header['level']} {header['title']}")
            
            summary_lines.append("")
            summary_lines.append("💡 提示: 使用以下工具操作模板:")
            summary_lines.append("- analyze_template: 分析完整模板结构")
            summary_lines.append("- get_section_content: 查看章节内容")
            summary_lines.append("- update_section_content: 更新章节内容")
            summary_lines.append("- add_new_section: 添加新章节")
            
            return '\n'.join(summary_lines)
            
        except Exception as e:
            logger.error(f"生成结构摘要失败: {e}")
            return f"生成结构摘要失败: {str(e)}"


# 创建全局实例
template_agent_tools = TemplateAgentTools()
