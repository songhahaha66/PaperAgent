"""
模板操作工具
为AI提供操作论文模板的便利接口
"""

import os
import logging
from typing import Dict, Any, Optional, List
from . import template_tools

logger = logging.getLogger(__name__)


class TemplateOperations:
    """模板操作工具类，为AI提供操作模板的便利接口"""
    
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
        try:
            # 提取模板结构
            structure = template_tools.extract_template_structure(template_content)
            
            if 'error' in structure:
                return f"模板分析失败: {structure['error']}"
            
            # 生成摘要
            summary = template_tools.get_template_summary(template_content)
            
            # 验证结构
            validation = template_tools.validate_template_structure(template_content)
            
            result_lines = []
            result_lines.append("=== 模板结构分析 ===")
            result_lines.append(summary)
            result_lines.append("")
            
            if validation['warnings']:
                result_lines.append("⚠️ 警告:")
                for warning in validation['warnings']:
                    result_lines.append(f"  - {warning}")
                result_lines.append("")
            
            if validation['errors']:
                result_lines.append("❌ 错误:")
                for error in validation['errors']:
                    result_lines.append(f"  - {error}")
                result_lines.append("")
            
            if validation['suggestions']:
                result_lines.append("💡 建议:")
                for suggestion in validation['suggestions']:
                    result_lines.append(f"  - {suggestion}")
                result_lines.append("")
            
            result_lines.append("=== 可用操作 ===")
            result_lines.append("1. 查看章节内容: get_section_content(章节标题)")
            result_lines.append("2. 更新章节内容: update_section_content(章节标题, 新内容, 模式)")
            result_lines.append("3. 添加新章节: add_new_section(父章节, 新章节标题, 内容)")
            result_lines.append("4. 删除章节: remove_section(章节标题)")
            result_lines.append("5. 重新排序: reorder_sections([章节标题列表])")
            result_lines.append("6. 格式化内容: format_template_content()")
            
            return '\n'.join(result_lines)
            
        except Exception as e:
            logger.error(f"分析模板失败: {e}")
            return f"模板分析失败: {str(e)}"
    
    async def get_section_content(self, template_content: str, section_title: str) -> str:
        """
        获取指定章节的内容
        
        Args:
            template_content: 模板内容
            section_title: 章节标题
            
        Returns:
            章节内容或错误信息
        """
        try:
            content = template_tools.get_section_content(template_content, section_title)
            if content is None:
                return f"未找到章节: {section_title}"
            
            return f"=== 章节: {section_title} ===\n{content}"
            
        except Exception as e:
            logger.error(f"获取章节内容失败: {e}")
            return f"获取章节内容失败: {str(e)}"
    
    async def update_section_content(self, template_content: str, section_title: str, new_content: str, mode: str = 'replace') -> str:
        """
        更新指定章节的内容
        
        Args:
            template_content: 模板内容
            section_title: 章节标题
            new_content: 新内容
            mode: 更新模式
            
        Returns:
            更新结果
        """
        try:
            # 检查章节是否存在
            section = template_tools.get_section_by_title(template_content, section_title)
            if not section:
                return f"未找到章节: {section_title}"
            
            # 更新内容
            updated_content = template_tools.update_section_content(
                template_content, section_title, new_content, mode
            )
            
            if updated_content == template_content:
                return f"更新失败: 无法更新章节 {section_title}"
            
            # 验证更新结果
            validation = template_tools.validate_template_structure(updated_content)
            
            result_lines = []
            result_lines.append(f"✅ 章节 '{section_title}' 更新成功")
            result_lines.append(f"更新模式: {mode}")
            result_lines.append("")
            
            if validation['warnings']:
                result_lines.append("⚠️ 更新后的警告:")
                for warning in validation['warnings']:
                    result_lines.append(f"  - {warning}")
            
            return '\n'.join(result_lines)
            
        except Exception as e:
            logger.error(f"更新章节内容失败: {e}")
            return f"更新章节内容失败: {str(e)}"
    
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
        try:
            # 检查父章节是否存在
            parent = template_tools.get_section_by_title(template_content, parent_section)
            if not parent:
                return f"未找到父章节: {parent_section}"
            
            # 检查新章节标题是否已存在
            existing = template_tools.get_section_by_title(template_content, section_title)
            if existing:
                return f"章节标题已存在: {section_title}"
            
            # 添加新章节
            updated_content = template_tools.add_new_section(
                template_content, parent_section, section_title, content
            )
            
            if updated_content == template_content:
                return f"添加章节失败: 无法添加章节 {section_title}"
            
            # 验证结果
            validation = template_tools.validate_template_structure(updated_content)
            
            result_lines = []
            result_lines.append(f"✅ 新章节 '{section_title}' 添加成功")
            result_lines.append(f"父章节: {parent_section}")
            result_lines.append(f"章节级别: {parent['level'] + 1}")
            if content:
                result_lines.append(f"内容长度: {len(content)} 字符")
            result_lines.append("")
            
            if validation['warnings']:
                result_lines.append("⚠️ 添加后的警告:")
                for warning in validation['warnings']:
                    result_lines.append(f"  - {warning}")
            
            return '\n'.join(result_lines)
            
        except Exception as e:
            logger.error(f"添加新章节失败: {e}")
            return f"添加新章节失败: {str(e)}"
    
    async def remove_section(self, template_content: str, section_title: str) -> str:
        """
        删除指定章节
        
        Args:
            template_content: 模板内容
            section_title: 章节标题
            
        Returns:
            删除结果
        """
        try:
            # 检查章节是否存在
            section = template_tools.get_section_by_title(template_content, section_title)
            if not section:
                return f"未找到要删除的章节: {section_title}"
            
            # 检查是否是顶级章节
            if section['level'] == 1:
                return f"无法删除顶级章节: {section_title}"
            
            # 删除章节
            updated_content = template_tools.remove_section(template_content, section_title)
            
            if updated_content == template_content:
                return f"删除章节失败: 无法删除章节 {section_title}"
            
            # 验证结果
            validation = template_tools.validate_template_structure(updated_content)
            
            result_lines = []
            result_lines.append(f"✅ 章节 '{section_title}' 删除成功")
            result_lines.append(f"原章节级别: {section['level']}")
            if section['has_content']:
                result_lines.append(f"原内容长度: {section['line_count']} 行")
            result_lines.append("")
            
            if validation['warnings']:
                result_lines.append("⚠️ 删除后的警告:")
                for warning in validation['warnings']:
                    result_lines.append(f"  - {warning}")
            
            return '\n'.join(result_lines)
            
        except Exception as e:
            logger.error(f"删除章节失败: {e}")
            return f"删除章节失败: {str(e)}"
    
    async def reorder_sections(self, template_content: str, section_order: List[str]) -> str:
        """
        重新排序章节
        
        Args:
            template_content: 模板内容
            section_order: 新的章节顺序列表
            
        Returns:
            重排序结果
        """
        try:
            # 验证所有章节都存在
            structure = template_tools.extract_template_structure(template_content)
            existing_sections = {s['title'] for s in structure['sections']}
            
            missing_sections = [title for title in section_order if title not in existing_sections]
            if missing_sections:
                return f"以下章节不存在: {', '.join(missing_sections)}"
            
            # 重新排序
            updated_content = template_tools.reorder_sections(template_content, section_order)
            
            if updated_content == template_content:
                return "重排序失败: 内容未发生变化"
            
            # 验证结果
            validation = template_tools.validate_template_structure(updated_content)
            
            result_lines = []
            result_lines.append("✅ 章节重排序成功")
            result_lines.append("新顺序:")
            for i, title in enumerate(section_order, 1):
                result_lines.append(f"  {i}. {title}")
            result_lines.append("")
            
            if validation['warnings']:
                result_lines.append("⚠️ 重排序后的警告:")
                for warning in validation['warnings']:
                    result_lines.append(f"  - {warning}")
            
            return '\n'.join(result_lines)
            
        except Exception as e:
            logger.error(f"重排序章节失败: {e}")
            return f"重排序章节失败: {str(e)}"
    
    async def format_template(self, template_content: str, format_options: Dict[str, Any] = None) -> str:
        """
        格式化模板内容
        
        Args:
            template_content: 模板内容
            format_options: 格式化选项
            
        Returns:
            格式化结果
        """
        try:
            # 格式化内容
            formatted_content = template_tools.format_template_content(template_content, format_options)
            
            if formatted_content == template_content:
                return "格式化完成: 内容无需格式化"
            
            # 验证结果
            validation = template_tools.validate_template_structure(formatted_content)
            
            result_lines = []
            result_lines.append("✅ 模板格式化完成")
            if format_options:
                result_lines.append("格式化选项:")
                for key, value in format_options.items():
                    result_lines.append(f"  - {key}: {value}")
            result_lines.append("")
            
            if validation['warnings']:
                result_lines.append("⚠️ 格式化后的警告:")
                for warning in validation['warnings']:
                    result_lines.append(f"  - {warning}")
            
            return '\n'.join(result_lines)
            
        except Exception as e:
            logger.error(f"格式化模板失败: {e}")
            return f"格式化模板失败: {str(e)}"
    
    async def get_template_help(self) -> str:
        """
        获取模板操作帮助信息
        
        Returns:
            帮助信息
        """
        help_text = """
=== 模板操作工具帮助 ===

🎯 主要功能:
1. 分析模板结构 - 了解模板的章节组织和内容分布
2. 查看章节内容 - 获取指定章节的详细内容
3. 更新章节内容 - 支持多种更新模式
4. 添加新章节 - 在指定父章节下创建新章节
5. 删除章节 - 移除不需要的章节
6. 重排序章节 - 调整章节顺序
7. 格式化内容 - 美化模板格式

📝 更新模式说明:
- replace: 完全替换章节内容
- append: 在章节末尾追加内容
- prepend: 在章节开头插入内容
- merge: 智能合并现有内容和新内容

🔧 使用建议:
- 操作前先使用 analyze_template 了解模板结构
- 更新内容时选择合适的模式
- 操作完成后验证模板结构的合理性
- 定期格式化模板保持整洁

💡 最佳实践:
- 保持章节层级的一致性
- 为每个章节添加适当的内容
- 使用描述性的章节标题
- 定期检查和优化模板结构
        """
        return help_text.strip()


# 创建全局实例
template_operations = TemplateOperations()
