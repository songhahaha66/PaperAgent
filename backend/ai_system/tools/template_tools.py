"""
模板操作工具模块
专门为AI提供操作论文模板的便利功能
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import os

logger = logging.getLogger(__name__)


class TemplateTools:
    """模板操作工具类，为AI提供操作模板的便利功能"""
    
    def __init__(self, workspace_dir: str = None):
        self.workspace_dir = workspace_dir or os.getenv("WORKSPACE_DIR", ".")
        
    def extract_template_structure(self, template_content: str) -> Dict[str, Any]:
        """
        提取模板结构，识别所有标题层级和内容
        
        Args:
            template_content: 模板内容字符串
            
        Returns:
            包含模板结构的字典
        """
        try:
            lines = template_content.split('\n')
            structure = {
                'title': '',
                'sections': [],
                'total_sections': 0,
                'max_depth': 0,
                'has_content': False
            }
            
            current_section = None
            content_lines = []
            
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if not line:
                    continue
                    
                # 检查是否是标题行
                header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
                if header_match:
                    # 保存前一个章节的内容
                    if current_section:
                        current_section['content'] = '\n'.join(content_lines).strip()
                        current_section['line_count'] = len(content_lines)
                        content_lines = []
                    
                    # 创建新章节
                    level = len(header_match.group(1))
                    title = header_match.group(2).strip()
                    
                    current_section = {
                        'level': level,
                        'title': title,
                        'line_number': line_num,
                        'content': '',
                        'line_count': 0,
                        'subsections': [],
                        'has_content': False
                    }
                    
                    structure['sections'].append(current_section)
                    structure['max_depth'] = max(structure['max_depth'], level)
                    
                    # 如果是顶级标题，设置为文档标题
                    if level == 1 and not structure['title']:
                        structure['title'] = title
                        
                elif current_section:
                    # 非标题行，添加到当前章节内容
                    content_lines.append(line)
                    if line.strip():
                        current_section['has_content'] = True
                        structure['has_content'] = True
            
            # 保存最后一个章节的内容
            if current_section and content_lines:
                current_section['content'] = '\n'.join(content_lines).strip()
                current_section['line_count'] = len(content_lines)
            
            structure['total_sections'] = len(structure['sections'])
            
            # 修复：有标题结构的模板应该被认为是有内容的
            if structure['total_sections'] > 0:
                structure['has_content'] = True
                # 同时更新所有章节的has_content状态
                for section in structure['sections']:
                    section['has_content'] = True
            
            # 构建章节层级关系
            self._build_section_hierarchy(structure['sections'])
            
            return structure
            
        except Exception as e:
            logger.error(f"提取模板结构失败: {e}")
            return {'error': f'提取模板结构失败: {str(e)}'}
    
    def _build_section_hierarchy(self, sections: List[Dict[str, Any]]):
        """构建章节层级关系"""
        if not sections:
            return
            
        stack = []
        
        for section in sections:
            # 弹出栈中层级大于等于当前章节的项
            while stack and stack[-1]['level'] >= section['level']:
                stack.pop()
            
            # 将当前章节添加到父章节的子章节列表
            if stack:
                stack[-1]['subsections'].append(section)
            else:
                # 顶级章节
                pass
                
            stack.append(section)
    
    def get_section_by_title(self, template_content: str, section_title: str, exact_match: bool = False) -> Optional[Dict[str, Any]]:
        """
        根据标题查找章节
        
        Args:
            template_content: 模板内容
            section_title: 要查找的章节标题
            exact_match: 是否精确匹配
            
        Returns:
            找到的章节信息，未找到返回None
        """
        try:
            structure = self.extract_template_structure(template_content)
            
            for section in structure['sections']:
                if exact_match:
                    if section['title'] == section_title:
                        return section
                else:
                    if section_title.lower() in section['title'].lower():
                        return section
                        
            return None
            
        except Exception as e:
            logger.error(f"查找章节失败: {e}")
            return None
    
    def get_section_content(self, template_content: str, section_title: str) -> Optional[str]:
        """
        获取指定章节的内容
        
        Args:
            template_content: 模板内容
            section_title: 章节标题
            
        Returns:
            章节内容，未找到返回None
        """
        section = self.get_section_by_title(template_content, section_title)
        return section['content'] if section else None
    
    def update_section_content(self, template_content: str, section_title: str, new_content: str, mode: str = 'replace') -> str:
        """
        更新指定章节的内容
        
        Args:
            template_content: 模板内容
            section_title: 章节标题
            new_content: 新内容
            mode: 更新模式 ('replace', 'append', 'prepend', 'merge')
            
        Returns:
            更新后的模板内容
        """
        try:
            section = self.get_section_by_title(template_content, section_title)
            if not section:
                logger.warning(f"未找到章节: {section_title}")
                return template_content
            
            lines = template_content.split('\n')
            section_start = section['line_number'] - 1  # 转换为0基索引
            
            # 找到章节结束位置（下一个同级或更高级标题）
            section_end = len(lines)
            for i in range(section_start + 1, len(lines)):
                line = lines[i].strip()
                if line.startswith('#'):
                    header_level = len(re.match(r'^(#{1,6})', line).group(1))
                    if header_level <= section['level']:
                        section_end = i
                        break
            
            # 根据模式更新内容
            if mode == 'replace':
                # 替换整个章节内容
                new_lines = [lines[section_start]]  # 保留标题行
                if new_content.strip():
                    new_lines.extend(new_content.split('\n'))
                new_lines.extend(lines[section_end:])
                
            elif mode == 'append':
                # 在章节末尾追加内容
                new_lines = lines[:section_end]
                if new_content.strip():
                    new_lines.extend([''] + new_content.split('\n'))
                new_lines.extend(lines[section_end:])
                
            elif mode == 'prepend':
                # 在章节开头插入内容
                new_lines = lines[:section_start + 1]
                if new_content.strip():
                    new_lines.extend(new_content.split('\n') + [''])
                new_lines.extend(lines[section_start + 1:])
                
            elif mode == 'merge':
                # 合并内容（智能合并）
                existing_content = '\n'.join(lines[section_start + 1:section_end]).strip()
                if existing_content and new_content.strip():
                    merged_content = f"{existing_content}\n\n{new_content}"
                else:
                    merged_content = existing_content or new_content
                
                new_lines = [lines[section_start]]
                if merged_content:
                    new_lines.extend(merged_content.split('\n'))
                new_lines.extend(lines[section_end:])
            else:
                logger.error(f"不支持的更新模式: {mode}")
                return template_content
            
            return '\n'.join(new_lines)
            
        except Exception as e:
            logger.error(f"更新章节内容失败: {e}")
            return template_content
    
    def add_new_section(self, template_content: str, parent_section: str, section_title: str, content: str = '', level: int = None) -> str:
        """
        在指定父章节下添加新章节
        
        Args:
            template_content: 模板内容
            parent_section: 父章节标题
            section_title: 新章节标题
            content: 新章节内容
            level: 新章节级别（自动计算如果未指定）
            
        Returns:
            更新后的模板内容
        """
        try:
            parent = self.get_section_by_title(template_content, parent_section)
            if not parent:
                logger.warning(f"未找到父章节: {parent_section}")
                return template_content
            
            # 自动计算新章节级别
            if level is None:
                level = parent['level'] + 1
            
            # 构建新章节
            new_section_lines = [f"{'#' * level} {section_title}"]
            if content.strip():
                new_section_lines.extend(['', content])
            new_section_lines.append('')
            
            # 找到父章节的结束位置
            lines = template_content.split('\n')
            parent_start = parent['line_number'] - 1
            
            # 找到父章节结束位置
            parent_end = len(lines)
            for i in range(parent_start + 1, len(lines)):
                line = lines[i].strip()
                if line.startswith('#'):
                    header_level = len(re.match(r'^(#{1,6})', line).group(1))
                    if header_level <= parent['level']:
                        parent_end = i
                        break
            
            # 在父章节末尾插入新章节
            new_lines = lines[:parent_end]
            new_lines.extend(new_section_lines)
            new_lines.extend(lines[parent_end:])
            
            return '\n'.join(new_lines)
            
        except Exception as e:
            logger.error(f"添加新章节失败: {e}")
            return template_content
    
    def remove_section(self, template_content: str, section_title: str) -> str:
        """
        删除指定章节
        
        Args:
            template_content: 模板内容
            section_title: 要删除的章节标题
            
        Returns:
            更新后的模板内容
        """
        try:
            section = self.get_section_by_title(template_content, section_title)
            if not section:
                logger.warning(f"未找到要删除的章节: {section_title}")
                return template_content
            
            lines = template_content.split('\n')
            section_start = section['line_number'] - 1
            
            # 找到章节结束位置
            section_end = len(lines)
            for i in range(section_start + 1, len(lines)):
                line = lines[i].strip()
                if line.startswith('#'):
                    header_level = len(re.match(r'^(#{1,6})', line).group(1))
                    if header_level <= section['level']:
                        section_end = i
                        break
            
            # 删除章节
            new_lines = lines[:section_start] + lines[section_end:]
            return '\n'.join(new_lines)
            
        except Exception as e:
            logger.error(f"删除章节失败: {e}")
            return template_content
    
    def reorder_sections(self, template_content: str, section_order: List[str]) -> str:
        """
        重新排序章节
        
        Args:
            template_content: 模板内容
            section_order: 新的章节顺序列表
            
        Returns:
            重新排序后的模板内容
        """
        try:
            structure = self.extract_template_structure(template_content)
            
            # 收集所有章节内容
            sections_content = {}
            for section in structure['sections']:
                sections_content[section['title']] = self.get_section_content(template_content, section['title'])
            
            # 按新顺序重建内容
            new_content_lines = []
            
            # 添加文档标题
            if structure['title']:
                new_content_lines.append(f"# {structure['title']}")
                new_content_lines.append('')
            
            # 按新顺序添加章节
            for section_title in section_order:
                if section_title in sections_content:
                    section = self.get_section_by_title(template_content, section_title)
                    if section:
                        # 添加章节标题
                        new_content_lines.append(f"{'#' * section['level']} {section_title}")
                        # 添加章节内容
                        content = sections_content[section_title]
                        if content:
                            new_content_lines.extend(['', content])
                        new_content_lines.append('')
            
            return '\n'.join(new_content_lines)
            
        except Exception as e:
            logger.error(f"重新排序章节失败: {e}")
            return template_content
    
    def validate_template_structure(self, template_content: str) -> Dict[str, Any]:
        """
        验证模板结构是否合理
        
        Args:
            template_content: 模板内容
            
        Returns:
            验证结果
        """
        try:
            structure = self.extract_template_structure(template_content)
            
            validation_result = {
                'is_valid': True,
                'warnings': [],
                'errors': [],
                'suggestions': []
            }
            
            # 检查是否有文档标题
            if not structure['title']:
                validation_result['warnings'].append('文档缺少顶级标题（# 标题）')
            
            # 检查章节层级是否合理
            for section in structure['sections']:
                if section['level'] > 6:
                    validation_result['errors'].append(f'章节级别过深: {section["title"]} (级别 {section["level"]})')
                
                if section['level'] > 1 and not any(s['level'] < section['level'] for s in structure['sections']):
                    validation_result['warnings'].append(f'章节 {section["title"]} 可能是孤立的子章节')
            
            # 检查是否有内容
            if not structure['has_content']:
                validation_result['warnings'].append('模板缺少实际内容，只有标题')
            
            # 检查章节数量
            if structure['total_sections'] < 2:
                validation_result['suggestions'].append('建议至少包含2个章节（标题和至少一个内容章节）')
            
            # 检查是否有错误
            if validation_result['errors']:
                validation_result['is_valid'] = False
            
            return validation_result
            
        except Exception as e:
            logger.error(f"验证模板结构失败: {e}")
            return {
                'is_valid': False,
                'errors': [f'验证失败: {str(e)}'],
                'warnings': [],
                'suggestions': []
            }
    
    def format_template_content(self, template_content: str, format_options: Dict[str, Any] = None) -> str:
        """
        格式化模板内容
        
        Args:
            template_content: 模板内容
            format_options: 格式化选项
            
        Returns:
            格式化后的内容
        """
        try:
            if not format_options:
                format_options = {
                    'add_spacing': True,
                    'normalize_headers': True,
                    'trim_whitespace': True,
                    'max_line_length': 80
                }
            
            lines = template_content.split('\n')
            formatted_lines = []
            
            for i, line in enumerate(lines):
                # 处理标题行
                if line.strip().startswith('#'):
                    if format_options.get('normalize_headers', True):
                        # 标准化标题格式
                        header_match = re.match(r'^(#{1,6})\s*(.+?)\s*$', line)
                        if header_match:
                            level = header_match.group(1)
                            title = header_match.group(2).strip()
                            line = f"{level} {title}"
                    
                    # 在标题前添加空行（除了第一个标题）
                    if i > 0 and format_options.get('add_spacing', True):
                        formatted_lines.append('')
                
                # 处理内容行
                else:
                    if format_options.get('trim_whitespace', True):
                        line = line.rstrip()
                    
                    # 限制行长度
                    if format_options.get('max_line_length') and len(line) > format_options['max_line_length']:
                        # 简单的行分割（可以改进为更智能的分割）
                        words = line.split()
                        current_line = ''
                        for word in words:
                            if len(current_line + ' ' + word) <= format_options['max_line_length']:
                                current_line += (' ' + word) if current_line else word
                            else:
                                if current_line:
                                    formatted_lines.append(current_line)
                                current_line = word
                        if current_line:
                            line = current_line
                
                formatted_lines.append(line)
            
            # 移除末尾多余的空行
            while formatted_lines and not formatted_lines[-1].strip():
                formatted_lines.pop()
            
            return '\n'.join(formatted_lines)
            
        except Exception as e:
            logger.error(f"格式化模板内容失败: {e}")
            return template_content
    
    def get_template_summary(self, template_content: str) -> str:
        """
        生成模板摘要
        
        Args:
            template_content: 模板内容
            
        Returns:
            模板摘要
        """
        try:
            structure = self.extract_template_structure(template_content)
            
            summary_lines = []
            summary_lines.append(f"模板标题: {structure['title'] or '未设置'}")
            summary_lines.append(f"总章节数: {structure['total_sections']}")
            summary_lines.append(f"最大层级: {structure['max_depth']}")
            summary_lines.append(f"包含内容: {'是' if structure['has_content'] else '否'}")
            summary_lines.append("")
            summary_lines.append("章节结构:")
            
            for section in structure['sections']:
                indent = "  " * (section['level'] - 1)
                summary_lines.append(f"{indent}{'#' * section['level']} {section['title']}")
                if section['has_content']:
                    summary_lines.append(f"{indent}  (包含内容，{section['line_count']} 行)")
            
            return '\n'.join(summary_lines)
            
        except Exception as e:
            logger.error(f"生成模板摘要失败: {e}")
            return f"生成摘要失败: {str(e)}"


# 创建全局实例
template_tools = TemplateTools()
