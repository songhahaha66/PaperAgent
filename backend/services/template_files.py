import os
import shutil
from pathlib import Path
from fastapi import HTTPException, status
from typing import Optional

class TemplateFileService:
    """模板文件管理服务"""
    
    def __init__(self, base_path: str = "../pa_data/temple"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def create_template_directory(self, template_id: int) -> Path:
        """为模板创建目录"""
        template_dir = self.base_path / str(template_id)
        template_dir.mkdir(exist_ok=True)
        return template_dir
    
    def save_template_file(self, template_id: int, filename: str, content: str) -> str:
        """保存模板文件"""
        template_dir = self.create_template_directory(template_id)
        file_path = template_dir / filename
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return str(file_path)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save template file: {str(e)}"
            )
    
    def get_template_file_content(self, template_id: int, filename: str) -> str:
        """获取模板文件内容"""
        file_path = self.base_path / str(template_id) / filename
        
        if not file_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template file {filename} not found"
            )
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read template file: {str(e)}"
            )
    
    def list_template_files(self, template_id: int) -> list:
        """列出模板目录下的所有文件"""
        template_dir = self.base_path / str(template_id)
        
        if not template_dir.exists():
            return []
        
        files = []
        for file_path in template_dir.iterdir():
            if file_path.is_file():
                files.append({
                    "filename": file_path.name,
                    "size": file_path.stat().st_size,
                    "modified": file_path.stat().st_mtime
                })
        
        return files
    
    def delete_template_file(self, template_id: int, filename: str) -> bool:
        """删除模板文件"""
        file_path = self.base_path / str(template_id) / filename
        
        if not file_path.exists():
            return False
        
        try:
            file_path.unlink()
            return True
        except Exception:
            return False
    
    def delete_template_directory(self, template_id: int) -> bool:
        """删除整个模板目录"""
        template_dir = self.base_path / str(template_id)
        
        if not template_dir.exists():
            return True
        
        try:
            shutil.rmtree(template_dir)
            return True
        except Exception:
            return False

# 创建全局实例
template_file_service = TemplateFileService()
