from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from models import models
from schemas import schemas
from services import crud
from auth import auth
from database.database import get_db
from typing import List
from ..utils import route_guard

router = APIRouter(prefix="/templates", tags=["模板管理"])

@router.post("", response_model=schemas.PaperTemplateResponse)
@route_guard
async def create_template(
    template: schemas.PaperTemplateCreateWithContent,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """创建论文模板"""
    # 提取文件内容
    content = template.content
    # 创建模板数据（不包含content字段）
    template_data = schemas.PaperTemplateCreate(
        name=template.name,
        description=template.description,
        category=template.category,
        file_path=template.file_path,
        is_public=template.is_public
    )
    return crud.create_paper_template(db, template_data, current_user, content)

@router.get("", response_model=List[schemas.PaperTemplateResponse])
@route_guard
async def get_user_templates(
    skip: int = 0,
    limit: int = 100,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """获取当前用户的模板"""
    return crud.get_user_templates(db, current_user, skip, limit)

@router.get("/public", response_model=List[schemas.PaperTemplateResponse])
@route_guard
async def get_public_templates(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """获取公开模板"""
    return crud.get_public_templates(db, skip, limit)

@router.get("/{template_id}", response_model=schemas.PaperTemplateResponse)
@route_guard
async def get_template(
    template_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """获取指定模板信息"""
    template = crud.get_paper_template(db, template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    # 检查权限：只有创建者或公开模板可以访问
    if not template.is_public and template.created_by != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this template"
        )
    
    return template

@router.put("/{template_id}", response_model=schemas.PaperTemplateResponse)
@route_guard
async def update_template(
    template_id: int,
    template_update: schemas.PaperTemplateUpdate,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """更新模板"""
    return crud.update_paper_template(db, template_id, template_update, current_user)

@router.delete("/{template_id}")
@route_guard
async def delete_template(
    template_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """删除模板"""
    return crud.delete_paper_template(db, template_id, current_user)

@router.delete("/{template_id}/force")
@route_guard
async def force_delete_template(
    template_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """强制删除模板（同时删除引用该模板的工作）"""
    return crud.force_delete_paper_template(db, template_id, current_user)

@router.get("/{template_id}/content")
@route_guard
async def get_template_content(
    template_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """获取模板文件内容"""
    content = crud.get_template_file_content(db, template_id, current_user)
    return {"content": content}
