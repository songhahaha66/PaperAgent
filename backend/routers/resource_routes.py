"""Read-only method resources, independent of any research project."""
from fastapi import APIRouter, Depends, HTTPException
from auth.auth import get_current_user
from ai_system.modules.methods import skill_catalog, skill_detail

router = APIRouter(prefix='/api/resources', tags=['resources'])


@router.get('/methods')
async def methods(user: int = Depends(get_current_user)):
    return {'methods': skill_catalog()}


@router.get('/methods/{key}')
async def method(key: str, user: int = Depends(get_current_user)):
    try:
        return skill_detail(key)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
