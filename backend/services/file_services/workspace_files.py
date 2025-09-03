import os
import json
import shutil
import zipfile
import tempfile
from pathlib import Path
from fastapi import HTTPException, status, UploadFile
from typing import List, Dict, Any, Optional
from .file_helper import FileHelper
from ..data_services.utils import handle_service_errors

class WorkspaceFileService:
    def __init__(self):
        self.base_path = Path("../pa_data/workspaces")
        self.helper = FileHelper(self.base_path)
    
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
    
    def list_files(self, work_id: str, relative_path: str = "", recursive: bool = True) -> List[Dict[str, Any]]:
        """列出工作空间中的文件，支持递归遍历
        
        Args:
            work_id: 工作ID
            relative_path: 相对路径，默认为根目录
            recursive: 是否递归遍历子目录，默认为True
        """
        try:
            workspace_path = self.ensure_workspace_exists(work_id)
            target_path = workspace_path / relative_path if relative_path else workspace_path
            
            if not target_path.exists():
                return []
            
            files = []
            
            if recursive:
                # 使用更直接的方法递归遍历
                def scan_directory(current_path: Path, current_depth: int = 0):
                    try:
                        for item in current_path.iterdir():
                            # 计算相对于工作空间的路径
                            rel_path = str(item.relative_to(workspace_path))
                            
                            # 计算深度
                            depth = current_depth
                            
                            file_info = {
                                "name": item.name,
                                "type": "directory" if item.is_dir() else "file",
                                "size": item.stat().st_size if item.is_file() else None,
                                "modified": item.stat().st_mtime,
                                "path": rel_path,
                                "depth": depth
                            }
                            files.append(file_info)
                            
                            # 调试输出
                            print(f"找到文件: {rel_path}, 类型: {file_info['type']}, 深度: {depth}")
                            
                            # 如果是目录且需要递归，继续扫描
                            if item.is_dir() and recursive:
                                scan_directory(item, current_depth + 1)
                    except Exception as e:
                        print(f"扫描目录 {current_path} 时出错: {e}")
                
                # 开始扫描
                scan_directory(target_path)
                
            else:
                # 只遍历当前目录
                for item in target_path.iterdir():
                    file_info = {
                        "name": item.name,
                        "type": "directory" if item.is_dir() else "file",
                        "size": item.stat().st_size if item.is_file() else None,
                        "modified": item.stat().st_mtime,
                        "path": str(item.relative_to(workspace_path)),
                        "depth": 0
                    }
                    files.append(file_info)
            
            # 按类型、深度和名称排序：目录在前，文件在后，同类型按深度和名称排序
            files.sort(key=lambda x: (x["type"] == "file", x["depth"], x["name"].lower()))
            
            # 添加调试日志
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"列出工作空间 {work_id} 的文件，路径: {relative_path}，递归: {recursive}，找到 {len(files)} 个文件")
            for file in files:
                logger.info(f"  - {file['type']}: {file['path']}")
            
            return files
        except HTTPException:
            raise
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"列出文件失败: {e}")
            print(f"错误: {e}")  # 直接打印错误
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to list files: {str(e)}"
            )
    
    @handle_service_errors()
    def read_file(self, work_id: str, file_path: str) -> str:
        """读取工作空间中的文件内容"""
        workspace_path = self.ensure_workspace_exists(work_id)
        # 允许图片走旧逻辑（返回base64）
        target_file = workspace_path / file_path
        if self._is_image_file(file_path):
            import base64
            if not target_file.exists() or not target_file.is_file():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
            if target_file.stat().st_size > 10 * 1024 * 1024:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large to read")
            with open(target_file, 'rb') as f:
                return base64.b64encode(f.read()).decode('utf-8')
        # 非图片：借助 FileHelper
        relative = str(Path(work_id) / file_path)
        # 这里不能用 helper 的 base 为 work_id，因为 helper 的 base 是 workspaces 根
        helper = FileHelper(self.base_path / work_id)
        return helper.read_text(Path(file_path).as_posix())
    
    def _is_image_file(self, file_path: str) -> bool:
        """判断文件是否为图片文件"""
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp']
        return any(file_path.lower().endswith(ext) for ext in image_extensions)
    
    @handle_service_errors()
    def write_file(self, work_id: str, file_path: str, content: str) -> Dict[str, Any]:
        """写入文件到工作空间"""
        self.ensure_workspace_exists(work_id)
        helper = FileHelper(self.base_path / work_id)
        written_path = helper.write_text(Path(file_path).as_posix(), content)
        return {
            "message": "File written successfully",
            "path": Path(written_path).relative_to(self.base_path / work_id).as_posix(),
            "size": len(content)
        }
    
    @handle_service_errors()
    def upload_file(self, work_id: str, file_path: str, file: UploadFile) -> Dict[str, Any]:
        """上传文件到工作空间"""
        self.ensure_workspace_exists(work_id)
        helper = FileHelper(self.base_path / work_id)
        written_path = helper.write_bytes_stream(Path(file_path).as_posix(), file.file)
        size = Path(written_path).stat().st_size
        return {
            "message": "File uploaded successfully",
            "path": Path(written_path).relative_to(self.base_path / work_id).as_posix(),
            "size": size,
            "filename": file.filename
        }
    
    @handle_service_errors()
    def delete_file(self, work_id: str, file_path: str) -> Dict[str, str]:
        """删除工作空间中的文件或目录"""
        workspace_path = self.ensure_workspace_exists(work_id)
        # 防止删除整个工作空间
        if file_path.strip() in ("", ".", "/"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete entire workspace")
        helper = FileHelper(workspace_path)
        helper.delete(Path(file_path).as_posix())
        return {"message": "File or directory deleted successfully"}
    
    @handle_service_errors()
    def create_directory(self, work_id: str, dir_path: str) -> Dict[str, str]:
        """在工作空间中创建目录"""
        workspace_path = self.ensure_workspace_exists(work_id)
        target_dir = workspace_path / dir_path
        if target_dir.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Directory already exists")
        target_dir.mkdir(parents=True, exist_ok=True)
        return {"message": "Directory created successfully"}
    
    @handle_service_errors()
    def get_file_info(self, work_id: str, file_path: str) -> Dict[str, Any]:
        """获取文件信息"""
        workspace_path = self.ensure_workspace_exists(work_id)
        target_file = workspace_path / file_path
        if not target_file.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        stat = target_file.stat()
        return {
            "name": target_file.name,
            "type": "directory" if target_file.is_dir() else "file",
            "size": stat.st_size if target_file.is_file() else None,
            "created": stat.st_ctime,
            "modified": stat.st_mtime,
            "path": str(target_file.relative_to(workspace_path))
        }
    
    def export_workspace(self, work_id: str) -> str:
        """导出工作空间为ZIP文件，返回临时文件路径"""
        try:
            workspace_path = self.ensure_workspace_exists(work_id)
            
            # 创建临时ZIP文件
            temp_dir = tempfile.mkdtemp()
            zip_filename = f"workspace_{work_id}.zip"
            zip_path = os.path.join(temp_dir, zip_filename)
            
            # 创建ZIP文件
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                # 遍历工作空间目录
                for root, dirs, files in os.walk(workspace_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # 计算相对路径
                        arcname = os.path.relpath(file_path, workspace_path)
                        zip_file.write(file_path, arcname)
                        
                # 如果工作空间为空，添加一个空的README文件
                if not os.listdir(workspace_path):
                    zip_file.writestr("README.txt", "This workspace is empty.")
            
            return zip_path
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to export workspace: {str(e)}"
            )

# 创建全局实例
workspace_file_service = WorkspaceFileService()
