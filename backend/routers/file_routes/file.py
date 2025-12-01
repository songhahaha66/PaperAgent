from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from auth import auth
from database.database import get_db
from pathlib import Path
from ..utils import route_guard

router = APIRouter(prefix="/files", tags=["文件管理"])

# 输出格式与文件扩展名的映射关系
OUTPUT_FORMAT_EXTENSIONS = {
    "latex": ".tex",
    "markdown": ".md",
    "word": ".docx"
}

@router.post("/upload")
@route_guard
async def upload_template_file(
    file: UploadFile = File(...),
    output_format: str = Form(...),
    current_user: int = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """上传模板文件并解析内容
    
    根据输出格式限制可上传的文件类型：
    - latex: 只能上传 .tex 文件
    - markdown: 只能上传 .md 文件
    - word: 只能上传 .docx 文件
    """
    # 验证输出格式
    if output_format not in OUTPUT_FORMAT_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的输出格式。支持的格式: {', '.join(OUTPUT_FORMAT_EXTENSIONS.keys())}"
        )
    
    # 获取文件扩展名
    file_extension = Path(file.filename).suffix.lower()
    
    # 获取该输出格式允许的文件扩展名
    allowed_extension = OUTPUT_FORMAT_EXTENSIONS[output_format]
    
    # 验证文件扩展名是否匹配输出格式
    if file_extension != allowed_extension:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"输出格式为 '{output_format}' 时，只能上传 {allowed_extension} 文件"
        )
    
    # 读取文件内容
    content = await file.read()
    if file_extension in ['.tex', '.md']:
        content_str = content.decode('utf-8')
    elif file_extension == '.docx':
        content_str = f"[Word文档内容 - {file.filename}]"
    
    file_path = file.filename
    return {
        "message": "文件上传成功",
        "file_path": file_path,
        "content": content_str,
        "output_format": output_format
    }
