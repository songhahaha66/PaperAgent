from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from models import models
from schemas import schemas
from auth import auth
from fastapi import HTTPException, status
from ..file_services.template_files import template_file_service
from .utils import ensure_owner, model_to_dict
from config.paths import get_workspace_path
import uuid
import json
import os
from pathlib import Path
from datetime import datetime
import asyncio
import logging

logger = logging.getLogger(__name__)

def get_user_by_email(db: Session, email: str):
    return db.query(models.User).filter(models.User.email == email).first()

def get_user_by_id(db: Session, user_id: int):
    return db.query(models.User).filter(models.User.id == user_id).first()

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
    """获取系统配置；新数据库里还没有这一行时按默认值（允许注册）创建"""
    config = db.query(models.SystemConfig).first()
    if config is None:
        config = models.SystemConfig(is_allow_register=True)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config

def update_system_config(db: Session, is_allow_register: bool):
    """更新系统配置"""
    config = get_system_config(db)
    config.is_allow_register = is_allow_register
    db.commit()
    db.refresh(config)
    return config

# ModelConfig相关的CRUD操作
def create_model_config(db: Session, config: schemas.ModelConfigCreate, user_id: int):
    """创建模型配置"""
    try:
        if config.type == 'jev' and not config.api_key.strip():
            raise HTTPException(status_code=400, detail='请填写 Jev API 密钥')
        # 检查是否已存在相同类型的配置（同一用户下）
        existing_config = db.query(models.ModelConfig).filter(
            models.ModelConfig.type == config.type,
            models.ModelConfig.created_by == user_id
        ).first()

        if existing_config:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model config for type '{config.type}' already exists for this user"
            )

        db_config = models.ModelConfig(
            type=config.type,
            model_id=config.model_id,
            base_url=config.base_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            created_by=user_id
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
            detail="模型配置保存失败，请重试"
        ) from None

def get_model_config(db: Session, config_id: int):
    """根据ID获取模型配置（不包含api_key）"""
    return db.query(models.ModelConfig).filter(models.ModelConfig.id == config_id).first()

def get_model_config_by_type(db: Session, config_type: str, user_id: int = None):
    """根据类型获取模型配置（不包含api_key）"""
    query = db.query(models.ModelConfig).filter(models.ModelConfig.type == config_type)
    if user_id is not None:
        query = query.filter(models.ModelConfig.created_by == user_id)
    return query.first()

def get_all_model_configs(db: Session, skip: int = 0, limit: int = 100, user_id: int = None):
    """获取所有模型配置（不包含api_key）"""
    query = db.query(models.ModelConfig)
    if user_id is not None:
        query = query.filter(models.ModelConfig.created_by == user_id)
    return query.offset(skip).limit(limit).all()

def update_model_config(db: Session, config_id: int, config_update: schemas.ModelConfigUpdate, user_id: int = None):
    """更新模型配置"""
    db_config = get_model_config(db, config_id)
    if not db_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found"
        )

    # 如果指定了用户ID，检查权限
    if user_id is not None and db_config.created_by != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this model config"
        )

    try:
        # 使用model_dump()替代dict()以兼容新版本Pydantic
        update_data = config_update.model_dump(exclude_unset=True) if hasattr(config_update, 'model_dump') else config_update.dict(exclude_unset=True)
        if 'api_key' in update_data and not (update_data['api_key'] or '').strip():
            update_data.pop('api_key')

        for field, value in update_data.items():
            setattr(db_config, field, value)

        db.commit()
        db.refresh(db_config)
        return db_config
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="模型配置保存失败，请重试"
        ) from None

def delete_model_config(db: Session, config_id: int, user_id: int = None):
    """删除模型配置"""
    db_config = get_model_config(db, config_id)
    if not db_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found"
        )

    # 如果指定了用户ID，检查权限
    if user_id is not None and db_config.created_by != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this model config"
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

def clear_all_model_configs(db: Session, user_id: int = None):
    """清空所有模型配置"""
    try:
        query = db.query(models.ModelConfig)
        if user_id is not None:
            query = query.filter(models.ModelConfig.created_by == user_id)
        query.delete()
        db.commit()
        return {"message": "All model configs cleared successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to clear model configs: {str(e)}"
        )

# 模板相关的CRUD操作
def create_paper_template(db: Session, template: schemas.PaperTemplateCreate, user_id: int, file_content: str = "", is_binary: bool = False):
    """创建论文模板
    
    Args:
        db: 数据库会话
        template: 模板创建数据
        user_id: 用户ID
        file_content: 文件内容（文本或base64编码的二进制）
        is_binary: 是否为二进制文件（如docx）
    """
    from ..file_services.template_contract import analyze_and_store_template, delete_template_analysis
    from config.paths import get_templates_path
    saved = None
    db_template = None
    try:
        if not file_content:
            raise ValueError('模板创建必须上传文件，不能指定服务端路径')
        data = template.model_dump(exclude={'file_path'})
        expected = {'markdown': '.md', 'word': '.docx', 'latex': '.tex'}.get(template.output_format)
        if not expected or Path(template.file_path).suffix.lower() != expected:
            raise ValueError('模板格式与文件类型不匹配')
        db_template = models.PaperTemplate(**data, file_path='', created_by=user_id)
        db.add(db_template)
        db.flush()
        saved = template_file_service.save_file(db_template.id, template.file_path, file_content, is_binary)
        db_template.file_path = saved
        analyze_and_store_template(get_templates_path()/saved, db_template.id, db_template.name, db_template.output_format)
        db.commit()
        db.refresh(db_template)
        return db_template
    except Exception as exc:
        db.rollback()
        if saved:
            template_file_service.delete_file(saved)
        if db_template is not None and db_template.id:
            delete_template_analysis(db_template.id)
        raise HTTPException(400, 'Template creation failed: ' + str(exc)) from exc


def get_paper_template(db: Session, template_id: int):
    """根据ID获取论文模板"""
    return db.query(models.PaperTemplate).filter(models.PaperTemplate.id == template_id).first()

def get_user_templates(db: Session, user_id: int, skip: int = 0, limit: int = 100, output_format: str = None):
    """获取指定用户的模板"""
    query = db.query(models.PaperTemplate).filter(models.PaperTemplate.created_by == user_id)
    if output_format:
        query = query.filter(models.PaperTemplate.output_format == output_format)
    return query.offset(skip).limit(limit).all()

def get_public_templates(db: Session, skip: int = 0, limit: int = 100, output_format: str = None):
    """获取公开模板"""
    query = db.query(models.PaperTemplate).filter(models.PaperTemplate.is_public == True)
    if output_format:
        query = query.filter(models.PaperTemplate.output_format == output_format)
    return query.offset(skip).limit(limit).all()

def update_paper_template(db: Session, template_id: int, template_update: schemas.PaperTemplateUpdate, user_id: int):
    """更新论文模板"""
    db_template = ensure_owner(
        get_paper_template(db, template_id),
        user_id,
        not_found_detail="Template not found",
        forbidden_detail="Not authorized to modify this template",
    )
    
    # 更新字段
    try:
        # 使用model_dump()替代dict()以兼容新版本Pydantic
        update_data = model_to_dict(template_update, exclude_unset=True)
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
    db_template = ensure_owner(
        get_paper_template(db, template_id),
        user_id,
        not_found_detail="Template not found",
        forbidden_detail="Not authorized to delete this template",
    )
    
    # Existing projects own independent copies. Detach only the library reference.
    db.query(models.Project).filter(models.Project.template_id == template_id).update({'template_id': None})

    # 删除关联的模板文件
    if db_template.file_path:
        template_file_service.delete_file(db_template.file_path)
    from ..file_services.template_contract import delete_template_analysis
    delete_template_analysis(template_id)
    
    # 删除数据库记录
    db.delete(db_template)
    db.commit()
    return {"message": "Template deleted successfully"}

def force_delete_paper_template(db: Session, template_id: int, user_id: int):
    """The historical endpoint is safe: deleting a library template never deletes projects."""
    return delete_paper_template(db, template_id, user_id)


def get_template_file_content(db: Session, template_id: int, user_id: int) -> str:
    """获取模板文件内容"""
    db_template = get_paper_template(db, template_id)
    if not db_template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    # 公开模板放行，否则需校验所有者
    if not db_template.is_public:
        ensure_owner(
            db_template,
            user_id,
            not_found_detail="Template not found",
            forbidden_detail="Not authorized to access this template",
        )
    
    try:
        return template_file_service.get_template_file_content(template_id)
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template file not found"
        )

# Work相关的CRUD操作

def generate_work_id():
    """生成唯一的工作ID"""
    return uuid.uuid4().hex

def create_work(db: Session, work: schemas.WorkCreate, user_id: int):
    """创建工作"""
    if work.output_mode not in {'markdown', 'latex', 'word'}:
        raise HTTPException(400, 'Invalid output mode')
    if work.template_id is not None:
        template = get_paper_template(db, work.template_id)
        if template is None:
            raise HTTPException(404, 'Template not found')
        if not template.is_public and template.created_by != user_id:
            raise HTTPException(403, 'Not authorized to use this template')
        if template.output_format != work.output_mode:
            raise HTTPException(400, '模板格式与项目输出模式不一致')
    base_path = None
    try:
        # 生成唯一工作ID
        work_id = generate_work_id()
        
        # 创建工作记录
        db_work = models.Project(
            work_id=work_id,
            title=work.title,
            description=work.description,
            tags=work.tags,
            template_id=work.template_id,  # 添加模板ID
            output_mode=work.output_mode,  # 添加输出模式
            created_by=user_id,
            architecture_version=2
        )
        
        db.add(db_work)
        db.flush()
        
        # 创建工作空间目录结构和初始文件
        from ..file_services.workspace_structure import WorkspaceStructureManager
        candidate_path = get_workspace_path(work_id)
        candidate_path.mkdir(parents=True, exist_ok=False)
        base_path = candidate_path
        WorkspaceStructureManager.create_workspace_structure(
            base_path, 
            work_id, 
            template_id=db_work.template_id,
            output_mode=db_work.output_mode  # 传递输出模式
        )
        
        db.commit()
        db.refresh(db_work)
        return db_work
    except Exception as e:
        db.rollback()
        if base_path is not None:
            import shutil
            from ..file_services.workspace_layout import private_root
            shutil.rmtree(base_path, ignore_errors=True)
            shutil.rmtree(private_root(base_path), ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project creation failed: {str(e)}"
        )


def get_work(db: Session, work_id: str):
    """根据工作ID获取工作"""
    return db.query(models.Project).filter(models.Project.work_id == work_id).first()

async def get_work_async(db: AsyncSession, work_id: str):
    """异步版本：根据工作ID获取工作"""
    # 使用异步select查询
    from sqlalchemy import select
    stmt = select(models.Project).where(models.Project.work_id == work_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

def get_user_works(db: Session, user_id: int, skip: int = 0, limit: int = 100, 
                   status: str = None, search: str = None):
    """获取用户的工作列表，支持筛选和搜索"""
    query = db.query(models.Project).filter(models.Project.created_by == user_id)
    
    # 状态筛选
    if status:
        query = query.filter(models.Project.status == status)
    
    # 搜索筛选
    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            (models.Project.title.ilike(search_filter)) |
            (models.Project.description.ilike(search_filter)) |
            (models.Project.tags.ilike(search_filter))
        )
    
    # 按更新时间倒序排列
    query = query.order_by(models.Project.updated_at.desc())
    
    total = query.count()
    works = query.offset(skip).limit(limit).all()
    
    return {
        "works": works,
        "total": total,
        "page": skip // limit + 1,
        "size": limit
    }

def update_work(db: Session, work_id: str, work_update: schemas.WorkUpdate, user_id: int):
    """更新工作信息"""
    db_work = ensure_owner(
        get_work(db, work_id),
        user_id,
        not_found_detail="Project not found",
        forbidden_detail="Not authorized to modify this work",
    )
    
    from ..file_services.project_access import require_current
    require_current(db_work)
    try:
        # 使用model_dump()替代dict()以兼容新版本Pydantic
        update_data = model_to_dict(work_update, exclude_unset=True)
        
        if 'template_id' in update_data or 'output_mode' in update_data:
            raise HTTPException(400, '模板和输出模式在项目创建时固定，请新建项目')
        for field, value in update_data.items():
            setattr(db_work, field, value)
        
        db.commit()
        db.refresh(db_work)
        return db_work
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project update failed: {str(e)}"
        )

def delete_work(db: Session, work_id: str, user_id: int):
    """删除工作"""
    db_work = ensure_owner(
        get_work(db, work_id),
        user_id,
        not_found_detail="Project not found",
        forbidden_detail="Not authorized to delete this work",
    )
    
    from ..file_services.project_access import require_current
    require_current(db_work)
    try:
        from ..file_services.workspace_files import workspace_file_service
        with workspace_file_service._editable(work_id):
            workspace_path = get_workspace_path(work_id)
            if workspace_path.exists():
                import shutil
                shutil.rmtree(workspace_path)
            db.delete(db_work)
            db.commit()
        return {"message": "Project deleted successfully"}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project deletion failed: {str(e)}"
        )

def update_work_status(db: Session, work_id: str, status: str, user_id: int = None):
    """更新工作状态和进度"""
    db_work = get_work(db, work_id)
    if not db_work:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if user_id:
        ensure_owner(
            db_work,
            user_id,
            not_found_detail="Project not found",
            forbidden_detail="Not authorized to modify this work",
        )
    
    try:
        db_work.status = status
        
        db.commit()
        db.refresh(db_work)
        return db_work
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project status update failed: {str(e)}"
        )


