from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from database.database import get_db
from auth.auth import get_current_user
from schemas import schemas
from services import crud
from typing import Optional

router = APIRouter(prefix="/api/works", tags=["works"])

@router.post("/", response_model=schemas.WorkResponse)
async def create_work(
    work: schemas.WorkCreate,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """创建工作"""
    try:
        return crud.create_work(db, work, current_user)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/", response_model=schemas.WorkListResponse)
async def get_works(
    skip: int = Query(0, ge=0, description="跳过记录数"),
    limit: int = Query(100, ge=1, le=1000, description="返回记录数"),
    status: Optional[str] = Query(None, description="工作状态筛选"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取用户的工作列表"""
    try:
        return crud.get_user_works(db, current_user, skip, limit, status, search)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{work_id}", response_model=schemas.WorkResponse)
async def get_work(
    work_id: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取工作详情"""
    try:
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )
        
        # 检查权限：只有创建者可以查看
        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this work"
            )
        
        return work
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.put("/{work_id}", response_model=schemas.WorkResponse)
async def update_work(
    work_id: str,
    work_update: schemas.WorkUpdate,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """更新工作信息"""
    try:
        return crud.update_work(db, work_id, work_update, current_user)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.delete("/{work_id}")
async def delete_work(
    work_id: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """删除工作"""
    try:
        return crud.delete_work(db, work_id, current_user)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.patch("/{work_id}/status")
async def update_work_status(
    work_id: str,
    status: str = Query(..., description="新的工作状态"),
    progress: Optional[int] = Query(None, ge=0, le=100, description="工作进度(0-100)"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """更新工作状态和进度"""
    try:
        return crud.update_work_status(db, work_id, status, progress, current_user)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{work_id}/metadata")
async def get_work_metadata(
    work_id: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取工作元数据文件内容"""
    try:
        # 检查工作是否存在
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )
        
        # 检查权限
        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this work"
            )
        
        # 读取元数据文件
        import json
        from pathlib import Path
        
        metadata_file = Path("../pa_data/workspaces") / work_id / "metadata.json"
        if not metadata_file.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work metadata not found"
            )
        
        with open(metadata_file, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        return metadata
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{work_id}/chat-history")
async def get_work_chat_history(
    work_id: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取工作对话历史"""
    try:
        # 检查工作是否存在
        work = crud.get_work(db, work_id)
        if not work:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Work not found"
            )
        
        # 检查权限
        if work.created_by != current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this work"
            )
        
        # 读取对话历史文件
        import json
        from pathlib import Path
        
        chat_file = Path("../pa_data/workspaces") / work_id / "chat_history.json"
        if not chat_file.exists():
            return {}
            return {"messages": [], "context": {}}
        
        with open(chat_file, 'r', encoding='utf-8') as f:
            chat_history = json.load(f)
        
        # 确保返回正确的格式
        if isinstance(chat_history, list):
            # 如果是旧格式（列表），转换为新格式
            return {"messages": chat_history, "context": {}}
        elif isinstance(chat_history, dict):
            # 如果是新格式（字典），直接返回
            return chat_history
        else:
            # 其他情况，返回默认格式
            return {"messages": [], "context": {}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )
