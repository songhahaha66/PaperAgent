from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from database.database import get_db
from auth.auth import get_current_user
from schemas import schemas
from services import crud
from typing import Optional
from ..utils import route_guard
import os
import uuid
from pathlib import Path
import json
from datetime import datetime

router = APIRouter(prefix="/api/works", tags=["works"])

@router.post("", response_model=schemas.WorkResponse)
@route_guard
async def create_work(
    work: schemas.WorkCreate,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """创建工作"""
    return crud.create_work(db, work, current_user)

@router.get("", response_model=schemas.WorkListResponse)
@route_guard
async def get_works(
    skip: int = Query(0, ge=0, description="跳过记录数"),
    limit: int = Query(100, ge=1, le=1000, description="返回记录数"),
    status: Optional[str] = Query(None, description="工作状态筛选"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取用户的工作列表"""
    return crud.get_user_works(db, current_user, skip, limit, status, search)

@router.get("/{work_id}", response_model=schemas.WorkResponse)
@route_guard
async def get_work(
    work_id: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取工作详情"""
    work = crud.get_work(db, work_id)
    if not work:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found")
    if work.created_by != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this work")
    return work

@router.put("/{work_id}", response_model=schemas.WorkResponse)
@route_guard
async def update_work(
    work_id: str,
    work_update: schemas.WorkUpdate,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """更新工作信息"""
    return crud.update_work(db, work_id, work_update, current_user)

@router.delete("/{work_id}")
@route_guard
async def delete_work(
    work_id: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """删除工作"""
    return crud.delete_work(db, work_id, current_user)

@router.patch("/{work_id}/status")
@route_guard
async def update_work_status(
    work_id: str,
    status: str = Query(..., description="新的工作状态"),
    progress: Optional[int] = Query(None, ge=0, le=100, description="工作进度(0-100)"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """更新工作状态和进度"""
    return crud.update_work_status(db, work_id, status, progress, current_user)

@router.get("/{work_id}/metadata")
@route_guard
async def get_work_metadata(
    work_id: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取工作元数据文件内容"""
    # 检查工作是否存在
    work = crud.get_work(db, work_id)
    if not work:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found")
    if work.created_by != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this work")
    import json
    from pathlib import Path
    metadata_file = Path("../pa_data/workspaces") / work_id / "metadata.json"
    if not metadata_file.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work metadata not found")
    with open(metadata_file, 'r', encoding='utf-8') as f:
        metadata = json.load(f)
    return metadata

@router.get("/{work_id}/chat-history")
@route_guard
async def get_work_chat_history(
    work_id: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取工作对话历史"""
    work = crud.get_work(db, work_id)
    if not work:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found")
    if work.created_by != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this work")
    import json
    from pathlib import Path
    chat_file = Path("../pa_data/workspaces") / work_id / "chat_history.json"
    if not chat_file.exists():
        return {}
    with open(chat_file, 'r', encoding='utf-8') as f:
        chat_history = json.load(f)
    if isinstance(chat_history, list):
        return {"messages": chat_history, "context": {}}
    if isinstance(chat_history, dict):
        return chat_history
    return {"messages": [], "context": {}}

# 附件相关路由
def get_attachment_dir(work_id: str) -> Path:
    """获取附件目录路径"""
    base_dir = Path("../pa_data/workspaces") / work_id / "attachment"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir

def get_file_type(filename: str) -> str:
    """根据文件扩展名获取文件类型"""
    ext = Path(filename).suffix.lower()
    type_map = {
        '.pdf': 'pdf',
        '.doc': 'word',
        '.docx': 'word',
        '.xls': 'excel',
        '.xlsx': 'excel',
        '.png': 'image',
        '.jpg': 'image',
        '.jpeg': 'image',
        '.gif': 'image',
        '.txt': 'text',
        '.md': 'markdown',
        '.zip': 'archive',
        '.rar': 'archive'
    }
    return type_map.get(ext, 'unknown')

@router.post("/{work_id}/attachment", response_model=schemas.AttachmentUploadResponse)
@route_guard
async def upload_attachment(
    work_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """上传附件到指定工作"""
    # 检查工作是否存在且属于当前用户
    work = crud.get_work(db, work_id)
    if not work:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found")
    if work.created_by != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this work")

    # 检查文件大小（50MB限制）
    max_size = 50 * 1024 * 1024  # 50MB
    file.file.seek(0, 2)  # 移动到文件末尾
    file_size = file.file.tell()
    file.file.seek(0)  # 重置到文件开头

    if file_size > max_size:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large (max 50MB)")

    # 使用原始文件名，处理文件名冲突
    original_filename = file.filename
    attachment_dir = get_attachment_dir(work_id)

    # 处理文件名冲突：如果文件已存在，添加序号
    base_name = Path(original_filename).stem
    file_ext = Path(original_filename).suffix
    counter = 0

    # 先尝试使用原始文件名
    final_filename = original_filename
    file_path = attachment_dir / final_filename

    # 如果文件已存在，添加序号
    while file_path.exists():
        counter += 1
        final_filename = f"{base_name}_{counter}{file_ext}"
        file_path = attachment_dir / final_filename

    # 保存文件
    try:
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to save file: {str(e)}")

    # 创建附件信息
    attachment_info = schemas.AttachmentInfo(
        filename=final_filename,
        original_filename=original_filename,
        file_type=get_file_type(original_filename),
        file_size=file_size,
        mime_type=file.content_type or "application/octet-stream",
        upload_time=datetime.now().isoformat()
    )

    return schemas.AttachmentUploadResponse(
        message="Attachment uploaded successfully",
        attachment=attachment_info
    )

@router.get("/{work_id}/attachments", response_model=schemas.AttachmentListResponse)
@route_guard
async def get_attachments(
    work_id: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取工作的附件列表"""
    # 检查工作是否存在且属于当前用户
    work = crud.get_work(db, work_id)
    if not work:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found")
    if work.created_by != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this work")

    # 获取附件目录
    attachment_dir = get_attachment_dir(work_id)

    # 列出所有附件
    attachments = []
    if attachment_dir.exists():
        for file_path in attachment_dir.iterdir():
            if file_path.is_file():
                # 从文件名中提取原始信息（这里简化处理）
                stat = file_path.stat()
                attachment_info = schemas.AttachmentInfo(
                    filename=file_path.name,
                    original_filename=file_path.name,  # 实际中可以存储映射关系
                    file_type=get_file_type(file_path.name),
                    file_size=stat.st_size,
                    mime_type="application/octet-stream",  # 实际中可以存储
                    upload_time=datetime.fromtimestamp(stat.st_mtime).isoformat()
                )
                attachments.append(attachment_info)

    return schemas.AttachmentListResponse(
        attachments=attachments,
        total=len(attachments)
    )

@router.delete("/{work_id}/attachment/{filename}")
@route_guard
async def delete_attachment(
    work_id: str,
    filename: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """删除指定附件"""
    # 检查工作是否存在且属于当前用户
    work = crud.get_work(db, work_id)
    if not work:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found")
    if work.created_by != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this work")

    # 构建文件路径
    attachment_dir = get_attachment_dir(work_id)
    file_path = attachment_dir / filename

    # 检查文件是否存在
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    # 删除文件
    try:
        file_path.unlink()
        return {"message": "Attachment deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete file: {str(e)}")

@router.get("/{work_id}/attachment/download/{filename}")
@route_guard
async def download_attachment(
    work_id: str,
    filename: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """下载指定附件"""
    # 检查工作是否存在且属于当前用户
    work = crud.get_work(db, work_id)
    if not work:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found")
    if work.created_by != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this work")

    # 构建文件路径
    attachment_dir = get_attachment_dir(work_id)
    file_path = attachment_dir / filename

    # 检查文件是否存在
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    # 返回文件响应
    try:
        def iterfile(file_path: Path):
            with open(file_path, mode="rb") as file_like:
                yield from file_like

        # 获取MIME类型
        import mimetypes
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type is None:
            mime_type = "application/octet-stream"

        return StreamingResponse(
            iterfile(file_path),
            media_type=mime_type,
            headers={
                "Content-Disposition": f"attachment; filename={file_path.name}"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to download file: {str(e)}")
