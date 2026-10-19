"""Personal experience library; owner comes exclusively from authentication."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from auth.auth import get_current_user
from services.research_experience import ExperienceScope

router = APIRouter(prefix='/api/experiences', tags=['experience'])


class Transition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['validate', 'adopt', 'withdraw']
    base_version: int = Field(ge=1)


class Enabled(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool


@router.get('')
async def library(q: str = '', user: int = Depends(get_current_user)):
    return ExperienceScope(user).list(q)


@router.put('/policy')
async def policy(request: Enabled, user: int = Depends(get_current_user)):
    ExperienceScope(user).set_enabled(request.enabled)
    return {'enabled': request.enabled}


@router.post('/rebuild')
async def rebuild(user: int = Depends(get_current_user)):
    return ExperienceScope(user).rebuild()


@router.post('/{experience_id}/transitions')
async def transition(experience_id: str, request: Transition, user: int = Depends(get_current_user)):
    try:
        return ExperienceScope(user).transition(experience_id, request.action, request.base_version)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.patch('/{experience_id}')
async def change(experience_id: str, request: Enabled, user: int = Depends(get_current_user)):
    try:
        ExperienceScope(user).change(experience_id, enabled=request.enabled)
    except KeyError:
        raise HTTPException(404, '经验不存在或不可访问')
    return {'enabled': request.enabled}


@router.delete('/{experience_id}')
async def delete(experience_id: str, user: int = Depends(get_current_user)):
    try:
        ExperienceScope(user).change(experience_id, delete=True)
    except KeyError:
        raise HTTPException(404, '经验不存在或不可访问')
    return {'deleted': True}
