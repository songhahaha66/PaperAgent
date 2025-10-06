from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, Form, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database.database import get_db
from auth.auth import get_current_user
from services import crud
from services.file_services.workspace_files import workspace_file_service
from typing import Optional
import os
import tempfile

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

@router.get("/{work_id}/files")
async def list_workspace_files(
    work_id: str,
    path: str = Query("", description="相对路径，默认为根目录"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """列出工作空间中的文件"""
    try:
        # 检查工作是否存在且用户有权限
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )
        
        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this workspace"
            )
        
        return workspace_file_service.list_files(work_id, path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{work_id}/files/{file_path:path}/download")
async def download_workspace_file(
    work_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """下载工作空间中的文件"""
    try:
        # 检查工作是否存在且用户有权限
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )

        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this workspace"
            )

        # 构建文件路径
        workspace_path = workspace_file_service.get_workspace_path(work_id)
        target_file = workspace_path / file_path

        if not target_file.exists() or not target_file.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )

        # 获取MIME类型
        import mimetypes
        mime_type, _ = mimetypes.guess_type(str(target_file))
        if mime_type is None:
            mime_type = "application/octet-stream"

        # 返回文件下载响应
        return FileResponse(
            path=str(target_file),
            media_type=mime_type,
            filename=target_file.name
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{work_id}/files/{file_path:path}")
async def read_workspace_file(
    work_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """读取工作空间中的文件内容"""
    try:
        # 检查工作是否存在且用户有权限
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )

        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this workspace"
            )

        # 使用新的文件读取方法，它会返回不同类型文件的处理结果
        file_data = workspace_file_service.read_file(work_id, file_path)

        # 如果是图片文件，返回base64内容
        if file_data["type"] == "image":
            return {
                "type": "image",
                "content": file_data["content"],
                "filename": file_data["filename"],
                "size": file_data["size"]
            }

        # 如果是文本文件，返回内容
        elif file_data["type"] == "text":
            return {
                "type": "text",
                "content": file_data["content"],
                "filename": file_data["filename"],
                "size": file_data["size"]
            }

        # 如果是二进制文件，返回元数据
        elif file_data["type"] == "binary":
            return {
                "type": "binary",
                "filename": file_data["filename"],
                "size": file_data["size"],
                "mime_type": file_data["mime_type"],
                "download_url": file_data["download_url"],
                "message": file_data["message"]
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{work_id}/images/{file_path:path}")
async def get_workspace_image(
    work_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取工作空间中的图片文件"""
    try:
        # 检查工作是否存在且用户有权限
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )
        
        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this workspace"
            )
        
        # 获取图片文件的完整路径
        workspace_path = workspace_file_service.get_workspace_path(work_id)
        image_path = workspace_path / file_path
        
        if not image_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Image file not found"
            )
        
        if not image_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path is not a file"
            )
        
        # 检查文件大小
        if image_path.stat().st_size > 50 * 1024 * 1024:  # 50MB限制
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Image file too large"
            )
        
        # 返回文件响应
        return FileResponse(
            path=str(image_path),
            media_type=f"image/{file_path.split('.')[-1].lower()}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/{work_id}/files/{file_path:path}")
async def write_workspace_file(
    work_id: str,
    file_path: str,
    content: str = Form(..., description="文件内容"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """写入文件到工作空间"""
    try:
        # 检查工作是否存在且用户有权限
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )
        
        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this workspace"
            )
        
        return workspace_file_service.write_file(work_id, file_path, content)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/{work_id}/upload")
async def upload_file_to_workspace(
    work_id: str,
    file_path: str = Form(..., description="目标文件路径"),
    file: UploadFile = Form(..., description="上传的文件"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """上传文件到工作空间"""
    try:
        # 检查工作是否存在且用户有权限
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )
        
        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this workspace"
            )
        
        return workspace_file_service.upload_file(work_id, file_path, file)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.delete("/{work_id}/files/{file_path:path}")
async def delete_workspace_file(
    work_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """删除工作空间中的文件或目录"""
    try:
        # 检查工作是否存在且用户有权限
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )
        
        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this workspace"
            )
        
        return workspace_file_service.delete_file(work_id, file_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/{work_id}/mkdir")
async def create_workspace_directory(
    work_id: str,
    dir_path: str = Form(..., description="目录路径"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """在工作空间中创建目录"""
    try:
        # 检查工作是否存在且用户有权限
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )
        
        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this workspace"
            )
        
        return workspace_file_service.create_directory(work_id, dir_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/{work_id}/paper")
async def get_paper_content(
    work_id: str,
    paper_name: str = Query("paper.md", description="论文文件名"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取论文内容"""
    try:
        # 检查工作是否存在且用户有权限
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )

        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this workspace"
            )

        content = workspace_file_service.get_paper_content(work_id, paper_name)
        return {"content": content}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/{work_id}/export")
async def export_workspace(
    work_id: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """导出工作空间为ZIP文件"""
    try:
        # 检查工作是否存在且用户有权限
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )

        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to export this workspace"
            )

        # 生成ZIP文件
        zip_path = workspace_file_service.export_workspace(work_id)

        # 返回ZIP文件
        return FileResponse(
            path=zip_path,
            media_type="application/zip",
            filename=f"workspace_{work_id}.zip",
            headers={"Content-Disposition": f"attachment; filename=workspace_{work_id}.zip"}
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )