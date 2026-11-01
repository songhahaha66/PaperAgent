from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from schemas import schemas
from services import crud
from auth import auth
from database.database import get_db
from services.user_profile import normalize_avatar

from ..utils import route_guard
from .passkey import router as passkey_router

router = APIRouter(prefix="/auth",tags=["认证"])
router.include_router(passkey_router)

@router.post("/register", response_model=schemas.UserResponse)
@route_guard
async def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    """用户注册接口"""
    # 检查系统配置是否允许注册
    system_config = crud.get_system_config(db)
    if not system_config.is_allow_register:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User registration is currently disabled"
        )

    return crud.create_user(db=db, user=user)

@router.post("/login", response_model=schemas.Token)
@route_guard
async def login(user_credentials: schemas.UserLogin, db: Session = Depends(get_db)):
    """用户登录接口"""
    user = crud.authenticate_user(db, user_credentials.email, user_credentials.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = auth.create_access_token(data={"user_id": user.id})
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=schemas.UserResponse)
@route_guard
async def get_current_user_info(current_user: int = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    """获取当前用户信息"""
    user = crud.get_user_by_id(db, current_user)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user


@router.patch("/me", response_model=schemas.UserResponse)
async def update_current_user_profile(
    profile: schemas.UserProfileUpdate,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    user = crud.get_user_by_id(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    changes = profile.model_dump(exclude_unset=True)
    if 'avatar_url' in changes:
        changes['avatar_url'] = normalize_avatar(changes['avatar_url'])
    if 'username' in changes:
        existing = crud.get_user_by_username(db, changes['username'])
        if existing and existing.id != user.id:
            raise HTTPException(status_code=409, detail="Username is already taken")
    for key, value in changes.items():
        setattr(user, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username is already taken") from exc
    db.refresh(user)
    return user
