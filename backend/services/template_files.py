import os
import shutil
from pathlib import Path
from fastapi import HTTPException, status
from typing import Optional

class TemplateFileService:
    """模板文件管理服务 - 简化版：一个模板对应一个文件"""
    
    def __init__(self, base_path: str = "../pa_data/templates"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def generate_file_path(self, template_id: int, filename: str = None) -> str:
        """生成模板文件路径"""
        if filename:
            # 如果提供了文件名，使用原文件名
            file_path = self.base_path / f"{template_id}_{filename}"
        else:
            # 否则使用默认的模板文件扩展名
            file_path = self.base_path / f"{template_id}_template.md"
        
        return str(file_path)
    
    def save_template_file(self, template_id: int, content: str, filename: str = None) -> str:
        """保存模板文件内容"""
        file_path = self.generate_file_path(template_id, filename)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return file_path
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save template file: {str(e)}"
            )
    
    def get_template_file_content(self, template_id: int, filename: str = None) -> str:
        """获取模板文件内容"""
        file_path = self.generate_file_path(template_id, filename)
        
        if not Path(file_path).exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template file not found for template {template_id}"
            )
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read template file: {str(e)}"
            )
    
    def delete_template_file(self, template_id: int, filename: str = None) -> bool:
        """删除模板文件"""
        file_path = self.generate_file_path(template_id, filename)
        
        if not Path(file_path).exists():
            return True
        
        try:
            Path(file_path).unlink()
            return True
        except Exception:
            return False
    
    def file_exists(self, template_id: int, filename: str = None) -> bool:
        """检查模板文件是否存在"""
        file_path = self.generate_file_path(template_id, filename)
        return Path(file_path).exists()

# 创建全局实例
template_file_service = TemplateFileService()
