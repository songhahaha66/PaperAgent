import os
import json
import shutil
from pathlib import Path
from fastapi import HTTPException, status, UploadFile
from typing import List, Dict, Any, Optional

class WorkspaceFileService:
    def __init__(self):
        self.base_path = Path("../pa_data/workspaces")
    
    def get_workspace_path(self, work_id: str) -> Path:
        """获取工作空间路径"""
        return self.base_path / work_id
    
    def ensure_workspace_exists(self, work_id: str) -> Path:
        """确保工作空间存在"""
        workspace_path = self.get_workspace_path(work_id)
        if not workspace_path.exists():
            # 创建工作空间目录
            workspace_path.mkdir(parents=True, exist_ok=True)
            # 创建新的目录结构
            self._create_workspace_structure(workspace_path)
        return workspace_path
    
    def _create_workspace_structure(self, workspace_path: Path):
        """创建工作空间的目录结构"""
        try:
            # 创建必要的目录 - 与代码执行器保持一致
            directories = [
                "generated_code",      # 生成的代码文件
                "outputs/plots",       # 图表输出
                "outputs/data",        # 数据输出
                "outputs/logs"         # 执行日志
            ]
            
            for directory in directories:
                dir_path = workspace_path / directory
                dir_path.mkdir(parents=True, exist_ok=True)
                
        except Exception as e:
            # 记录错误但不中断流程
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"创建工作空间目录结构失败: {e}")
    
    def list_files(self, work_id: str, relative_path: str = "") -> List[Dict[str, Any]]:
        """列出工作空间中的文件"""
        try:
            workspace_path = self.ensure_workspace_exists(work_id)
            target_path = workspace_path / relative_path if relative_path else workspace_path
            
            if not target_path.exists():
                return []
            
            files = []
            for item in target_path.iterdir():
                file_info = {
                    "name": item.name,
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                    "modified": item.stat().st_mtime,
                    "path": str(item.relative_to(workspace_path))
                }
                files.append(file_info)
            
            # 按类型和名称排序：目录在前，文件在后
            files.sort(key=lambda x: (x["type"] == "file", x["name"].lower()))
            return files
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to list files: {str(e)}"
            )
    
    def read_file(self, work_id: str, file_path: str) -> str:
        """读取工作空间中的文件内容"""
        try:
            workspace_path = self.ensure_workspace_exists(work_id)
            target_file = workspace_path / file_path
            
            if not target_file.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found"
                )
            
            if not target_file.is_file():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Path is not a file"
                )
            
            # 检查文件大小，避免读取过大的文件
            if target_file.stat().st_size > 10 * 1024 * 1024:  # 10MB限制
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="File too large to read"
                )
            
            with open(target_file, 'r', encoding='utf-8') as f:
                return f.read()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read file: {str(e)}"
            )
    
    def write_file(self, work_id: str, file_path: str, content: str) -> Dict[str, Any]:
        """写入文件到工作空间"""
        try:
            workspace_path = self.ensure_workspace_exists(work_id)
            target_file = workspace_path / file_path
            
            # 确保父目录存在
            target_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(target_file, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return {
                "message": "File written successfully",
                "path": str(target_file.relative_to(workspace_path)),
                "size": len(content)
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to write file: {str(e)}"
            )
    
    def upload_file(self, work_id: str, file_path: str, file: UploadFile) -> Dict[str, Any]:
        """上传文件到工作空间"""
        try:
            workspace_path = self.ensure_workspace_exists(work_id)
            target_file = workspace_path / file_path
            
            # 确保父目录存在
            target_file.parent.mkdir(parents=True, exist_ok=True)
            
            # 检查文件大小
            if file.size and file.size > 50 * 1024 * 1024:  # 50MB限制
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="File too large (max 50MB)"
                )
            
            # 保存文件
            with open(target_file, 'wb') as f:
                shutil.copyfileobj(file.file, f)
            
            return {
                "message": "File uploaded successfully",
                "path": str(target_file.relative_to(workspace_path)),
                "size": target_file.stat().st_size,
                "filename": file.filename
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to upload file: {str(e)}"
            )
    
    def delete_file(self, work_id: str, file_path: str) -> Dict[str, str]:
        """删除工作空间中的文件或目录"""
        try:
            workspace_path = self.ensure_workspace_exists(work_id)
            target_path = workspace_path / file_path
            
            if not target_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File or directory not found"
                )
            
            # 防止删除整个工作空间
            if target_path == workspace_path:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot delete entire workspace"
                )
            
            if target_path.is_file():
                target_path.unlink()
            else:
                shutil.rmtree(target_path)
            
            return {"message": "File or directory deleted successfully"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete file: {str(e)}"
            )
    
    def create_directory(self, work_id: str, dir_path: str) -> Dict[str, str]:
        """在工作空间中创建目录"""
        try:
            workspace_path = self.ensure_workspace_exists(work_id)
            target_dir = workspace_path / dir_path
            
            if target_dir.exists():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Directory already exists"
                )
            
            target_dir.mkdir(parents=True, exist_ok=True)
            return {"message": "Directory created successfully"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create directory: {str(e)}"
            )
    
    def get_file_info(self, work_id: str, file_path: str) -> Dict[str, Any]:
        """获取文件信息"""
        try:
            workspace_path = self.ensure_workspace_exists(work_id)
            target_file = workspace_path / file_path
            
            if not target_file.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="File not found"
                )
            
            stat = target_file.stat()
            return {
                "name": target_file.name,
                "type": "directory" if target_file.is_dir() else "file",
                "size": stat.st_size if target_file.is_file() else None,
                "created": stat.st_ctime,
                "modified": stat.st_mtime,
                "path": str(target_file.relative_to(workspace_path))
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get file info: {str(e)}"
            )

# 创建全局实例
workspace_file_service = WorkspaceFileService()
