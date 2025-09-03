from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from models import models
from schemas import schemas
from services import crud
from auth import auth
from database.database import get_db

from ..utils import route_guard

router = APIRouter(prefix="/auth",tags=["认证"])

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
