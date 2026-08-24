from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from auth import auth
from auth import passkey as passkey_service
from database.database import get_db
from models import models
from schemas import schemas
from services import crud

from ..utils import route_guard

router = APIRouter(prefix="/passkey", tags=["Passkey"])


def _require_user(user_id: int, db: Session) -> models.User:
    user = crud.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("/register/options", response_model=schemas.PasskeyOptionsResponse)
@route_guard
async def passkey_register_options(
    request: Request,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """生成绑定 Passkey 的 WebAuthn 选项"""
    user = _require_user(current_user, db)
    return passkey_service.create_registration_options(db, user, request)


@router.post("/register/verify", response_model=schemas.PasskeyCredentialResponse)
@route_guard
async def passkey_register_verify(
    payload: schemas.PasskeyRegisterVerifyRequest,
    request: Request,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """校验并保存新 Passkey"""
    user = _require_user(current_user, db)
    return passkey_service.verify_registration(
        db,
        user,
        request,
        payload.challenge_id,
        payload.credential,
        payload.name,
    )


@router.post("/login/options", response_model=schemas.PasskeyOptionsResponse)
@route_guard
async def passkey_login_options(
    request: Request,
    payload: schemas.PasskeyLoginOptionsRequest | None = None,
    db: Session = Depends(get_db),
):
    """生成 Passkey 登录选项（可不填邮箱，走可发现凭证）"""
    email = payload.email if payload else None
    return passkey_service.create_authentication_options(db, request, email)


@router.post("/login/verify", response_model=schemas.Token)
@route_guard
async def passkey_login_verify(
    payload: schemas.PasskeyLoginVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """校验 Passkey 断言并签发登录 token"""
    user = passkey_service.verify_authentication(
        db, request, payload.challenge_id, payload.credential
    )
    access_token = auth.create_access_token(data={"user_id": user.id})
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/credentials", response_model=list[schemas.PasskeyCredentialResponse])
@route_guard
async def list_passkeys(
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """列出当前用户的 Passkey"""
    return passkey_service.list_credentials(db, current_user)


@router.patch("/credentials/{credential_id}", response_model=schemas.PasskeyCredentialResponse)
@route_guard
async def rename_passkey(
    credential_id: int,
    payload: schemas.PasskeyUpdateRequest,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """重命名 Passkey"""
    return passkey_service.rename_credential(db, current_user, credential_id, payload.name)


@router.delete("/credentials/{credential_id}")
@route_guard
async def delete_passkey(
    credential_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """删除 Passkey"""
    passkey_service.delete_credential(db, current_user, credential_id)
    return {"status": "success"}
