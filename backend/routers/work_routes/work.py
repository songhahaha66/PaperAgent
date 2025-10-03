from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from database.database import get_db
from auth.auth import get_current_user
from schemas import schemas
from services import crud
from typing import Optional
from ..utils import route_guard

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
