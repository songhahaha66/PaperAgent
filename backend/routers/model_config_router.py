from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database.database import get_db
from services import crud
from schemas import schemas
from auth import auth

router = APIRouter(prefix="/model-configs", tags=["模型配置"])

@router.post("/", response_model=schemas.ModelConfigResponse)
async def create_model_config(
    config: schemas.ModelConfigCreate,
    db: Session = Depends(get_db),
    current_user_email: str = Depends(auth.get_current_user)
):
    """创建模型配置"""
    return crud.create_model_config(db=db, config=config)

@router.get("/", response_model=list[schemas.ModelConfigResponse])
async def get_all_model_configs(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user_email: str = Depends(auth.get_current_user)
):
    """获取所有模型配置（不包含api_key）"""
    return crud.get_all_model_configs(db=db, skip=skip, limit=limit)

@router.get("/{config_id}", response_model=schemas.ModelConfigResponse)
async def get_model_config(
    config_id: int,
    db: Session = Depends(get_db),
    current_user_email: str = Depends(auth.get_current_user)
):
    """根据ID获取模型配置（不包含api_key）"""
    db_config = crud.get_model_config(db=db, config_id=config_id)
    if not db_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found"
        )
    return db_config

@router.get("/type/{config_type}", response_model=schemas.ModelConfigResponse)
async def get_model_config_by_type(
    config_type: str,
    db: Session = Depends(get_db),
    current_user_email: str = Depends(auth.get_current_user)
):
    """根据类型获取模型配置（不包含api_key）"""
    db_config = crud.get_model_config_by_type(db=db, config_type=config_type)
    if not db_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model config for type '{config_type}' not found"
        )
    return db_config

@router.put("/{config_id}", response_model=schemas.ModelConfigResponse)
async def update_model_config(
    config_id: int,
    config_update: schemas.ModelConfigUpdate,
    db: Session = Depends(get_db),
    current_user_email: str = Depends(auth.get_current_user)
):
    """更新模型配置"""
    return crud.update_model_config(db=db, config_id=config_id, config_update=config_update)

@router.delete("/{config_id}")
async def delete_model_config(
    config_id: int,
    db: Session = Depends(get_db),
    current_user_email: str = Depends(auth.get_current_user)
):
    """删除模型配置"""
    return crud.delete_model_config(db=db, config_id=config_id)

@router.delete("/", response_model=dict)
async def clear_all_model_configs(
    db: Session = Depends(get_db),
    current_user_email: str = Depends(auth.get_current_user)
):
    """清空所有模型配置"""
    return crud.clear_all_model_configs(db=db)
