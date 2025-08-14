from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from models import models
from schemas import schemas
from services import crud
from auth import auth
from database.database import get_db
from typing import Dict, Any, Optional

def _remove_api_key_from_config(config) -> Optional[Dict[str, Any]]:
    """从模型配置中移除 api_key 字段"""
    if config is None:
        return None
    
    return {
        "id": config.id,
        "type": config.type,
        "model_id": config.model_id,
        "base_url": config.base_url,
        "is_active": config.is_active,
        "created_at": config.created_at
    }

router = APIRouter(prefix="/model-configs", tags=["模型配置"])

@router.post("/", response_model=schemas.ModelConfigResponse)
async def create_model_config(
    config: schemas.ModelConfigCreate,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """创建模型配置"""
    result = crud.create_model_config(db=db, config=config)
    return _remove_api_key_from_config(result)

@router.get("/", response_model=list[schemas.ModelConfigResponse])
async def get_all_model_configs(
    skip: int = 0,
    limit: int = 100,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """获取所有模型配置（不包含api_key）"""
    configs = crud.get_all_model_configs(db=db, skip=skip, limit=limit)
    return [_remove_api_key_from_config(config) for config in configs]

@router.get("/{config_id}", response_model=schemas.ModelConfigResponse)
async def get_model_config(
    config_id: int,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """根据ID获取模型配置（不包含api_key）"""
    db_config = crud.get_model_config(db=db, config_id=config_id)
    if not db_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found"
        )
    return _remove_api_key_from_config(db_config)

@router.get("/type/{config_type}", response_model=schemas.ModelConfigResponse)
async def get_model_config_by_type(
    config_type: str,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """根据类型获取模型配置（不包含api_key）"""
    db_config = crud.get_model_config_by_type(db=db, config_type=config_type)
    if not db_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model config for type '{config_type}' not found"
        )
    return _remove_api_key_from_config(db_config)

@router.put("/{config_id}", response_model=schemas.ModelConfigResponse)
async def update_model_config(
    config_id: int,
    config_update: schemas.ModelConfigUpdate,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """更新模型配置"""
    result = crud.update_model_config(db=db, config_id=config_id, config_update=config_update)
    return _remove_api_key_from_config(result)

@router.delete("/{config_id}")
async def delete_model_config(
    config_id: int,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """删除模型配置"""
    return crud.delete_model_config(db=db, config_id=config_id)

@router.delete("/", response_model=dict)
async def clear_all_model_configs(
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """清空所有模型配置"""
    return crud.clear_all_model_configs(db=db)
