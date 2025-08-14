from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from models import models
from schemas import schemas
from auth import auth
from fastapi import HTTPException, status

def get_user_by_email(db: Session, email: str):
    return db.query(models.User).filter(models.User.email == email).first()

def get_user_by_username(db: Session, username: str):
    return db.query(models.User).filter(models.User.username == username).first()

def create_user(db: Session, user: schemas.UserCreate):
    # 检查邮箱是否已存在
    if get_user_by_email(db, user.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # 检查用户名是否已存在
    if get_user_by_username(db, user.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken"
        )
    
    # 创建新用户
    hashed_password = auth.get_password_hash(user.password)
    db_user = models.User(
        email=user.email,
        username=user.username,
        password_hash=hashed_password
    )
    
    try:
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User creation failed"
        )

def authenticate_user(db: Session, email: str, password: str):
    user = get_user_by_email(db, email)
    if not user:
        return False
    if not auth.verify_password(password, user.password_hash):
        return False
    return user

def get_system_config(db: Session):
    """获取系统配置"""
    config = db.query(models.SystemConfig).first()
    return config

def update_system_config(db: Session, is_allow_register: bool):
    """更新系统配置"""
    config = get_system_config(db)
    config.is_allow_register = is_allow_register
    db.commit()
    db.refresh(config)
    return config

# 模板相关的CRUD操作
def create_paper_template(db: Session, template: schemas.PaperTemplateCreate, user_id: int):
    """创建论文模板"""
    db_template = models.PaperTemplate(
        **template.dict(),
        created_by=user_id
    )
    db.add(db_template)
    db.commit()
    db.refresh(db_template)
    return db_template

def get_paper_template(db: Session, template_id: int):
    """根据ID获取论文模板"""
    return db.query(models.PaperTemplate).filter(models.PaperTemplate.id == template_id).first()

def get_user_templates(db: Session, user_id: int, skip: int = 0, limit: int = 100):
    """获取指定用户的模板"""
    return db.query(models.PaperTemplate).filter(
        models.PaperTemplate.created_by == user_id
    ).offset(skip).limit(limit).all()

def get_public_templates(db: Session, skip: int = 0, limit: int = 100):
    """获取公开模板"""
    return db.query(models.PaperTemplate).filter(
        models.PaperTemplate.is_public == True
    ).offset(skip).limit(limit).all()

def update_paper_template(db: Session, template_id: int, template_update: schemas.PaperTemplateUpdate, user_id: int):
    """更新论文模板"""
    db_template = get_paper_template(db, template_id)
    if not db_template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    # 检查权限：只有创建者可以修改
    if db_template.created_by != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this template"
        )
    
    # 更新字段
    update_data = template_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_template, field, value)
    
    db.commit()
    db.refresh(db_template)
    return db_template

def delete_paper_template(db: Session, template_id: int, user_id: int):
    """删除论文模板"""
    db_template = get_paper_template(db, template_id)
    if not db_template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    # 检查权限：只有创建者可以删除
    if db_template.created_by != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this template"
        )
    
    db.delete(db_template)
    db.commit()
    return {"message": "Template deleted successfully"}
