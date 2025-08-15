from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, Form
from sqlalchemy.orm import Session
from database.database import get_db
from auth.auth import get_current_user
from services import crud
from services.workspace_files import workspace_file_service
from typing import Optional

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
        
        return {"content": workspace_file_service.read_file(work_id, file_path)}
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

@router.get("/{work_id}/files/{file_path:path}/info")
async def get_file_info(
    work_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取文件信息"""
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
        
        return workspace_file_service.get_file_info(work_id, file_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )
