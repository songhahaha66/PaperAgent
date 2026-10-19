from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from models import models
from schemas import schemas
from services import crud
from auth import auth
from database.database import get_db
from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field
from types import SimpleNamespace
import asyncio
from ..utils import route_guard

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

@router.post("", response_model=schemas.ModelConfigResponse)
@route_guard
async def create_model_config(
    config: schemas.ModelConfigCreate,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """创建模型配置"""
    result = crud.create_model_config(db=db, config=config, user_id=current_user)
    return _remove_api_key_from_config(result)

@router.get("", response_model=list[schemas.ModelConfigResponse])
@route_guard
async def get_all_model_configs(
    skip: int = 0,
    limit: int = 100,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """获取当前用户的所有模型配置（不包含api_key）"""
    configs = crud.get_all_model_configs(db=db, skip=skip, limit=limit, user_id=current_user)
    return [_remove_api_key_from_config(config) for config in configs]

class ConnectionTest(BaseModel):
    type: Literal["brain", "code", "writing"] = "brain"
    model_id: str = Field(min_length=1, max_length=200)
    base_url: str = ""
    api_key: Optional[str] = None


@router.get("/readiness")
async def model_readiness(current_user: int = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    """Resolve the same user/env configuration as execution, returning booleans only."""
    from ai_system.config.environment import DatabaseConfigManager
    manager = DatabaseConfigManager(db)
    roles = {}
    for role in ("brain", "code", "writing"):
        try:
            config = manager.get_model_config(role, current_user)
            roles[role] = bool(config.api_key and config.model_id)
        except ValueError:
            roles[role] = False
    return {"roles": roles, "ready": all(roles.values())}


@router.post("/apply-default", response_model=list[schemas.ModelConfigResponse])
@route_guard
async def apply_default_model(
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Copy this user's analysis connection to the other roles without exposing its key."""
    source = crud.get_model_config_by_type(db, "brain", current_user)
    if not source or not source.api_key:
        raise HTTPException(status_code=400, detail="请先保存默认模型连接")
    try:
        for role in ("code", "writing"):
            target = crud.get_model_config_by_type(db, role, current_user)
            if target is None:
                target = models.ModelConfig(type=role, created_by=current_user)
                db.add(target)
            for attr in ("model_id", "base_url", "api_key", "provider", "is_active"):
                if hasattr(source, attr):
                    setattr(target, attr, getattr(source, attr))
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="应用模型配置失败，请重试") from None
    return [_remove_api_key_from_config(c) for c in crud.get_all_model_configs(db, user_id=current_user)]


@router.post("/test")
@route_guard
async def test_model_connection(
    config: ConnectionTest,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """One short real request; use only the current user's stored credential as fallback."""
    saved = crud.get_model_config_by_type(db, config.type, current_user)
    key = (config.api_key or "").strip() or (saved.api_key if saved else "")
    if not key:
        raise HTTPException(status_code=400, detail="请填写 API 密钥")
    from ai_system.core_handlers.llm_providers import create_llm_from_model_config
    connection = SimpleNamespace(model_id=config.model_id.strip(), base_url=config.base_url.strip(), api_key=key, provider=getattr(saved, "provider", "openai"))
    try:
        llm = create_llm_from_model_config(connection, streaming=False, max_tokens=32, timeout=20, max_retries=0)
        await asyncio.wait_for(llm.ainvoke("Reply with OK."), timeout=25)
    except (asyncio.TimeoutError, TimeoutError):
        raise HTTPException(status_code=504, detail="连接超时，请检查服务地址后重试") from None
    except Exception as exc:
        # Provider exception strings can contain credentials or request bodies.
        code = getattr(exc, "status_code", None)
        detail = {401: "密钥无效或已过期", 403: "此密钥没有模型访问权限", 404: "模型或 API 地址不存在", 429: "服务限流或余额不足"}.get(code, "模型请求失败，请检查配置或稍后重试")
        raise HTTPException(status_code=400, detail=detail) from None
    return {"ok": True}


@router.get("/{config_id}", response_model=schemas.ModelConfigResponse)
@route_guard
async def get_model_config(
    config_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """根据ID获取模型配置（不包含api_key）"""
    db_config = crud.get_model_config(db=db, config_id=config_id)
    if not db_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found"
        )
    # 检查权限：只有配置的创建者才能查看
    if db_config.created_by != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this model config"
        )
    return _remove_api_key_from_config(db_config)

@router.get("/type/{config_type}", response_model=schemas.ModelConfigResponse)
@route_guard
async def get_model_config_by_type(
    config_type: str,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """根据类型获取当前用户的模型配置（不包含api_key）"""
    db_config = crud.get_model_config_by_type(db=db, config_type=config_type, user_id=current_user)
    if not db_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model config for type '{config_type}' not found"
        )
    return _remove_api_key_from_config(db_config)

@router.put("/{config_id}", response_model=schemas.ModelConfigResponse)
@route_guard
async def update_model_config(
    config_id: int,
    config_update: schemas.ModelConfigUpdate,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """更新模型配置"""
    result = crud.update_model_config(db=db, config_id=config_id, config_update=config_update, user_id=current_user)
    return _remove_api_key_from_config(result)

@router.delete("/{config_id}")
@route_guard
async def delete_model_config(
    config_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """删除模型配置"""
    return crud.delete_model_config(db=db, config_id=config_id, user_id=current_user)

@router.delete("", response_model=dict)
@route_guard
async def clear_all_model_configs(
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """清空当前用户的所有模型配置"""
    return crud.clear_all_model_configs(db=db, user_id=current_user)
