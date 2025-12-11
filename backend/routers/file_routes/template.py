from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from models import models
from schemas import schemas
from services import crud
from auth import auth
from database.database import get_db
from typing import List, Optional
from ..utils import route_guard
import base64
import mimetypes
from pathlib import Path

router = APIRouter(prefix="/templates", tags=["模板管理"])

# 输出格式与文件扩展名的映射关系
OUTPUT_FORMAT_EXTENSIONS = {
    "latex": ".tex",
    "markdown": ".md",
    "word": ".docx"
}

@router.post("/upload", response_model=schemas.PaperTemplateResponse)
@route_guard
async def create_template_with_file(
    file: UploadFile = File(...),
    name: str = Form(...),
    output_format: str = Form(...),
    description: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    is_public: bool = Form(False),
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """创建模板（直接上传文件）- 一步完成"""
    # 验证输出格式
    if output_format not in OUTPUT_FORMAT_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的输出格式。支持的格式: {', '.join(OUTPUT_FORMAT_EXTENSIONS.keys())}"
        )
    
    # 获取文件扩展名
    file_extension = Path(file.filename).suffix.lower()
    allowed_extension = OUTPUT_FORMAT_EXTENSIONS[output_format]
    
    # 验证文件扩展名
    if file_extension != allowed_extension:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"输出格式为 '{output_format}' 时，只能上传 {allowed_extension} 文件"
        )
    
    # 读取文件内容
    content = await file.read()
    
    # 检查文件大小（限制为10MB）
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件大小超过限制（最大10MB）"
        )
    
    # 判断是否为二进制文件
    is_binary = file_extension == '.docx'
    
    # 处理文件内容
    if is_binary:
        content_str = base64.b64encode(content).decode('utf-8')
    else:
        try:
            content_str = content.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="文件编码错误，请使用UTF-8编码"
            )
    
    # 创建模板数据
    template_data = schemas.PaperTemplateCreate(
        name=name,
        description=description,
        category=category,
        output_format=output_format,
        file_path=file.filename,
        is_public=is_public
    )
    
    return crud.create_paper_template(db, template_data, current_user, content_str, is_binary)

@router.get("", response_model=List[schemas.PaperTemplateResponse])
@route_guard
async def get_user_templates(
    skip: int = 0,
    limit: int = 100,
    output_format: str = None,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """获取当前用户的模板"""
    return crud.get_user_templates(db, current_user, skip, limit, output_format)

@router.get("/public", response_model=List[schemas.PaperTemplateResponse])
@route_guard
async def get_public_templates(
    skip: int = 0,
    limit: int = 100,
    output_format: str = None,
    db: Session = Depends(get_db)
):
    """获取公开模板"""
    return crud.get_public_templates(db, skip, limit, output_format)

@router.get("/{template_id}", response_model=schemas.PaperTemplateResponse)
@route_guard
async def get_template(
    template_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """获取指定模板信息"""
    template = crud.get_paper_template(db, template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    # 检查权限：只有创建者或公开模板可以访问
    if not template.is_public and template.created_by != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this template"
        )
    
    return template

@router.put("/{template_id}", response_model=schemas.PaperTemplateResponse)
@route_guard
async def update_template(
    template_id: int,
    template_update: schemas.PaperTemplateUpdate,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """更新模板"""
    return crud.update_paper_template(db, template_id, template_update, current_user)

@router.delete("/{template_id}")
@route_guard
async def delete_template(
    template_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """删除模板"""
    return crud.delete_paper_template(db, template_id, current_user)

@router.delete("/{template_id}/force")
@route_guard
async def force_delete_template(
    template_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """强制删除模板（同时删除引用该模板的工作）"""
    return crud.force_delete_paper_template(db, template_id, current_user)

@router.get("/{template_id}/preview")
@route_guard
async def get_template_preview(
    template_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """获取模板文件预览内容，支持不同文件类型"""
    # 检查模板权限
    template = crud.get_paper_template(db, template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    # 检查权限：只有创建者或公开模板可以访问
    if not template.is_public and template.created_by != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this template"
        )
    
    # 获取模板文件路径
    from config.paths import get_templates_path
    
    # file_path 现在只存储文件名
    if template.file_path:
        file_path = get_templates_path() / template.file_path
    else:
        file_path = get_templates_path() / f"{template_id}_template.md"
    
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template file not found"
        )
    
    # 检测文件类型
    file_type = _detect_template_file_type(str(file_path))
    
    if file_type == 'text':
        # 文本文件：直接读取文件内容
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return {
                "type": "text",
                "content": content,
                "filename": file_path.name,
                "size": file_path.stat().st_size
            }
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read template content: {str(e)}"
            )
    
    else:  # binary (docx)
        # 二进制文件：返回元数据和下载信息
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if not mime_type:
            mime_type = "application/octet-stream"
        
        return {
            "type": "binary",
            "filename": file_path.name,
            "size": file_path.stat().st_size,
            "mime_type": mime_type,
            "download_url": f"/templates/{template_id}/download",
            "message": "Binary file - use download button to view"
        }

@router.get("/{template_id}/download")
@route_guard
async def download_template_file(
    template_id: int,
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """下载模板文件"""
    # 检查模板权限
    template = crud.get_paper_template(db, template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    # 检查权限：只有创建者或公开模板可以访问
    if not template.is_public and template.created_by != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this template"
        )
    
    # 获取模板文件路径
    from config.paths import get_templates_path
    
    # file_path 现在只存储文件名
    if template.file_path:
        file_path = get_templates_path() / template.file_path
    else:
        file_path = get_templates_path() / f"{template_id}_template.md"
    
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template file not found"
        )
    
    # 获取MIME类型
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"
    
    # 返回文件下载响应
    return FileResponse(
        path=str(file_path),
        media_type=mime_type,
        filename=file_path.name
    )

def _detect_template_file_type(file_path: str) -> str:
    """检测模板文件类型：返回 'text' 或 'binary'"""
    ext = Path(file_path).suffix.lower()
    # 模板系统只支持这三种格式
    if ext in {'.md', '.tex'}:
        return 'text'
    else:  # .docx 等
        return 'binary'
