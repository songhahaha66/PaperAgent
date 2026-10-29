from services.file_services.workspace_layout import workspace_path
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from database.database import get_db
from auth.auth import get_current_user
from schemas import schemas
from services import crud
from typing import Optional
from ..utils import route_guard
from config.paths import get_workspace_path
import os
import uuid
from pathlib import Path
import json
from datetime import datetime

router = APIRouter(prefix="/api/works", tags=["works"])


def _project_progress(work):
    if not work:
        return work
    if work.architecture_version != 2:
        return schemas.WorkResponse.model_validate(work)
    from ai_system.research.runtime import execution_reason
    from ai_system.research.store import ResearchStore, DB_PATH
    root=get_workspace_path(work.work_id)
    progress=0
    thread=workspace_path(root, '.system/graph_thread.json')
    if thread.is_file() and (workspace_path(root, DB_PATH)).is_file():
        saved=json.loads(thread.read_text())
        if saved.get('engine_version')=='research-v3':
            snapshot=ResearchStore(root,saved['thread_id'],read_only=True).snapshot()
            questions=(snapshot or {}).get('questions',{})
            answered={t['question_id'] for t in (snapshot or {}).get('experiments',{}).values()
                      if not t['retired'] and t['acceptance']=='accepted' and t['validity']=='current'}
            if questions:progress=round(100*len(answered)/len(questions))
    # A read projection must not dirty the ORM session or infer scientific
    # completion from a document's size or an old Markdown status label.
    return schemas.WorkResponse.model_validate(work).model_copy(update={
        'progress':progress, 'execution_reason':execution_reason(root,work.status)})

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
    response = crud.get_user_works(db, current_user, skip, limit, status, search)
    if isinstance(response, dict) and isinstance(response.get("works"), list):
        response["works"] = [_project_progress(work) for work in response["works"]]
    return response

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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if work.created_by != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this work")
    return _project_progress(work)

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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if work.created_by != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this work")
    from services.chat_services.chat_history_manager import ChatHistoryManager
    root = get_workspace_path(work_id)
    return ChatHistoryManager(str(root.parent)).get_work_history(work_id)

# 附件相关路由
def get_attachment_dir(work_id: str) -> Path:
    """获取附件目录路径"""
    base_dir = get_workspace_path(work_id) / "attachment"
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if work.created_by != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this work")

    from services.file_services.project_access import require_current
    from services.file_services.workspace_files import workspace_file_service
    from services.file_services.workspace_layout import relative_parts
    require_current(work)
    original_filename = file.filename or ''
    try:
        if len(relative_parts(original_filename)) != 1:
            raise ValueError('filename')
    except ValueError:
        raise HTTPException(400, 'Invalid attachment filename') from None
    final_filename = original_filename
    relative = 'attachment/' + final_filename
    if workspace_file_service.resolve(work_id, relative).exists():
        raise HTTPException(409, '文件名已存在')
    saved = workspace_file_service.upload_file(work_id, relative, file)
    file_size = saved['size']

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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if work.created_by != current_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this work")

    # 构建文件路径
    attachment_dir = get_attachment_dir(work_id)
    from services.file_services.workspace_files import workspace_file_service
    return workspace_file_service.delete_file(work_id, "attachment/" + filename)
