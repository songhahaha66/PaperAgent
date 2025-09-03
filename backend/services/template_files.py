import os
import shutil
from pathlib import Path
from fastapi import HTTPException, status
from typing import Optional
from .file_helper import FileHelper
from .utils import handle_service_errors

class TemplateFileService:
    """模板文件管理服务 - 简化版：一个模板对应一个文件"""
    
    def __init__(self, base_path: str = "../pa_data/templates"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.helper = FileHelper(self.base_path)
    
    def generate_file_path(self, template_id: int, filename: str = None) -> str:
        """生成模板文件路径"""
        if filename:
            # 如果提供了文件名，使用原文件名
            file_path = self.base_path / f"{template_id}_{filename}"
        else:
            # 否则使用默认的模板文件扩展名
            file_path = self.base_path / f"{template_id}_template.md"
        
        return str(file_path)
    
    @handle_service_errors()
    def save_template_file(self, template_id: int, content: str, filename: str = None) -> str:
        """保存模板文件内容"""
        relative = Path(self.generate_file_path(template_id, filename)).name
        # 写入
        return self.helper.write_text(relative, content)
    
    @handle_service_errors()
    def get_template_file_content(self, template_id: int, filename: str = None) -> str:
        """获取模板文件内容"""
        relative = Path(self.generate_file_path(template_id, filename)).name
        return self.helper.read_text(relative)
    
    @handle_service_errors()
    def delete_template_file(self, template_id: int, filename: str = None) -> bool:
        """删除模板文件"""
        relative = Path(self.generate_file_path(template_id, filename)).name
        # 若不存在则视为成功
        target = self.helper.resolve(relative)
        if not target.exists():
            return True
        if target.is_file():
            target.unlink()
        else:
            import shutil
            shutil.rmtree(target)
        return True
    


# 创建全局实例
template_file_service = TemplateFileService()
