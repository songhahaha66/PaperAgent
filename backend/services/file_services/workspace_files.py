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
            # 只创建工作空间目录，不创建子目录
            workspace_path.mkdir(parents=True, exist_ok=True)
        return workspace_path

    def list_files(self, work_id: str, relative_path: str = "", recursive: bool = True) -> List[Dict[str, Any]]:
        """列出工作空间中的文件（新版本：按分类返回）

        Args:
            work_id: 工作ID
            relative_path: 相对路径，默认为根目录
            recursive: 是否递归遍历子目录，默认为True
        """
        # 直接调用新的分类方法，然后转换为平面列表格式
        categorized_files = self.list_files_by_category(work_id)
        result = []

        for category, files in categorized_files.items():
            for file_info in files:
                result.append(file_info)

        return result

    def list_files_by_category(self, work_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """按分类列出工作空间中的文件

        Args:
            work_id: 工作ID

        Returns:
            包含四个分类的字典：{'code': [...], 'logs': [...], 'outputs': [...], 'papers': [...]}
        """
        try:
            workspace_path = self.ensure_workspace_exists(work_id)

            # 确保三个分类文件夹存在（不包括papers）
            categories = {
                'code': workspace_path / 'code',
                'logs': workspace_path / 'logs',
                'outputs': workspace_path / 'outputs'
            }

            # 创建分类文件夹（如果不存在）
            for category_name, category_path in categories.items():
                if not category_path.exists():
                    category_path.mkdir(parents=True, exist_ok=True)

            result = {}

            # 处理三个分类文件夹
            for category_name, category_path in categories.items():
                files = []

                if category_path.exists():
                    # 扫描分类文件夹
                    for item in category_path.rglob('*'):
                        if item.is_file():
                            # 计算相对路径
                            rel_path = str(item.relative_to(category_path))
                            full_path = str(item.relative_to(workspace_path))

                            file_info = {
                                "name": item.name,
                                "type": "file",
                                "size": item.stat().st_size,
                                "modified": item.stat().st_mtime,
                                "path": full_path,
                                "category_path": rel_path,
                                "category": category_name
                            }
                            files.append(file_info)

  
                # 按名称排序
                files.sort(key=lambda x: x["name"].lower())
                result[category_name] = files

            # 特殊处理papers分类：扫描根目录的paper.md文件
            papers_files = []
            paper_md = workspace_path / 'paper.md'
            if paper_md.exists():
                file_info = {
                    "name": paper_md.name,
                    "type": "file",
                    "size": paper_md.stat().st_size,
                    "modified": paper_md.stat().st_mtime,
                    "path": "paper.md",
                    "category_path": "paper.md",
                    "category": "papers"
                }
                papers_files.append(file_info)

            result["papers"] = papers_files

            return result

        except HTTPException:
            raise
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"按分类列出文件失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to list files by category: {str(e)}"
            )

    def get_paper_content(self, work_id: str, paper_name: str = "paper.md") -> str:
        """获取论文内容

        Args:
            work_id: 工作ID
            paper_name: 论文文件名，默认为paper.md

        Returns:
            论文文件内容
        """
        try:
            workspace_path = self.ensure_workspace_exists(work_id)
            paper_path = workspace_path / paper_name

            if not paper_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Paper file '{paper_name}' not found"
                )

            # 读取文件内容
            helper = FileHelper(workspace_path)
            return helper.read_text(str(paper_path.relative_to(workspace_path)))

        except HTTPException:
            raise
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"读取论文内容失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read paper content: {str(e)}"
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