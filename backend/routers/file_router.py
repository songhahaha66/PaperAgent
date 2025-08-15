from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session
from auth import auth
from database.database import get_db
from pathlib import Path

router = APIRouter(prefix="/files", tags=["文件管理"])

@router.post("/upload")
async def upload_template_file(
    file: UploadFile = File(...),
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """上传模板文件并解析内容"""
    # 检查文件类型
    allowed_extensions = ['.tex', '.md', '.txt', '.doc', '.docx']
    file_extension = Path(file.filename).suffix.lower()
    
    if file_extension not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型。支持的类型: {', '.join(allowed_extensions)}"
        )
    
    try:
        # 读取文件内容
        content = await file.read()
        
        # 根据文件类型处理内容
        if file_extension in ['.tex', '.md', '.txt']:
            # 文本文件直接解码
            content_str = content.decode('utf-8')
        elif file_extension in ['.doc', '.docx']:
            # Word文档需要特殊处理（这里简化处理）
            content_str = f"[Word文档内容 - {file.filename}]"
            # 在实际应用中，可以使用python-docx等库来解析Word文档
        
        # 生成文件路径
        file_path = file.filename
        
        return {
            "message": "文件上传成功",
            "file_path": file_path,
            "content": content_str
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文件处理失败: {str(e)}"
        )
