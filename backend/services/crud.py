from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from models import models
from schemas import schemas
from auth import auth
from fastapi import HTTPException, status
from .template_files import template_file_service

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

# ModelConfig相关的CRUD操作
def create_model_config(db: Session, config: schemas.ModelConfigCreate):
    """创建模型配置"""
    try:
        # 检查是否已存在相同类型的配置
        existing_config = db.query(models.ModelConfig).filter(
            models.ModelConfig.type == config.type
        ).first()
        
        if existing_config:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model config for type '{config.type}' already exists"
            )
        
        db_config = models.ModelConfig(
            type=config.type,
            model_id=config.model_id,
            base_url=config.base_url,
            api_key=config.api_key
        )
        
        db.add(db_config)
        db.commit()
        db.refresh(db_config)
        return db_config
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model config creation failed: {str(e)}"
        )

def get_model_config(db: Session, config_id: int):
    """根据ID获取模型配置（不包含api_key）"""
    return db.query(models.ModelConfig).filter(models.ModelConfig.id == config_id).first()

def get_model_config_by_type(db: Session, config_type: str):
    """根据类型获取模型配置（不包含api_key）"""
    return db.query(models.ModelConfig).filter(models.ModelConfig.type == config_type).first()

def get_all_model_configs(db: Session, skip: int = 0, limit: int = 100):
    """获取所有模型配置（不包含api_key）"""
    return db.query(models.ModelConfig).offset(skip).limit(limit).all()

def update_model_config(db: Session, config_id: int, config_update: schemas.ModelConfigUpdate):
    """更新模型配置"""
    db_config = get_model_config(db, config_id)
    if not db_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found"
        )
    
    try:
        # 使用model_dump()替代dict()以兼容新版本Pydantic
        update_data = config_update.model_dump(exclude_unset=True) if hasattr(config_update, 'model_dump') else config_update.dict(exclude_unset=True)
        
        for field, value in update_data.items():
            setattr(db_config, field, value)
        
        db.commit()
        db.refresh(db_config)
        return db_config
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model config update failed: {str(e)}"
        )

def delete_model_config(db: Session, config_id: int):
    """删除模型配置"""
    db_config = get_model_config(db, config_id)
    if not db_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found"
        )
    
    try:
        db.delete(db_config)
        db.commit()
        return {"message": "Model config deleted successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model config deletion failed: {str(e)}"
        )

def clear_all_model_configs(db: Session):
    """清空所有模型配置"""
    try:
        db.query(models.ModelConfig).delete()
        db.commit()
        return {"message": "All model configs cleared successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to clear model configs: {str(e)}"
        )

# 模板相关的CRUD操作
def create_paper_template(db: Session, template: schemas.PaperTemplateCreate, user_id: int, file_content: str = ""):
    """创建论文模板"""
    try:
        # 使用model_dump()替代dict()以兼容新版本Pydantic
        template_data = template.model_dump() if hasattr(template, 'model_dump') else template.dict()
        
        # 创建模板记录
        db_template = models.PaperTemplate(
            **template_data,
            created_by=user_id
        )
        db.add(db_template)
        db.commit()
        db.refresh(db_template)
        
        # 创建对应的模板文件
        if file_content:
            file_path = template_file_service.save_template_file(
                db_template.id, 
                file_content
            )
            # 更新数据库中的文件路径
            db_template.file_path = file_path
            db.commit()
            db.refresh(db_template)
        
        return db_template
    except Exception as e:
        db.rollback()
        # 如果创建失败，尝试删除已创建的文件
        if 'db_template' in locals() and hasattr(db_template, 'id'):
            template_file_service.delete_template_file(db_template.id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Template creation failed: {str(e)}"
        )

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
    try:
        # 使用model_dump()替代dict()以兼容新版本Pydantic
        update_data = template_update.model_dump(exclude_unset=True) if hasattr(template_update, 'model_dump') else template_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_template, field, value)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Template update failed: {str(e)}"
        )
    
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
    
    # 删除关联的模板文件
    template_file_service.delete_template_file(template_id)
    
    # 删除数据库记录
    db.delete(db_template)
    db.commit()
    return {"message": "Template deleted successfully"}

def get_template_file_content(db: Session, template_id: int, user_id: int) -> str:
    """获取模板文件内容"""
    db_template = get_paper_template(db, template_id)
    if not db_template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    # 检查权限：只有创建者或公开模板可以访问
    if not db_template.is_public and db_template.created_by != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this template"
        )
    
    try:
        return template_file_service.get_template_file_content(template_id)
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template file not found"
        )


