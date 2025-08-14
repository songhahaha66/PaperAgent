from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from models import models
from schemas import schemas
from services import crud
from services.template_files import template_file_service
from auth import auth
from database import database
from database.database import engine, get_db
from fastapi.middleware.cors import CORSMiddleware
from typing import List
# 创建数据库表
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="PaperAgent API",
    description="API for PaperAgent - an AI-powered paper generation system",
    version="0.1.0"
)
origins = [
    "*",  # * 表示允许所有来源（生产环境最好不要用 *）
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,         # 允许访问的域名列表
    allow_credentials=True,        # 是否允许携带 cookie
    allow_methods=["*"],           # 允许的 HTTP 方法，比如 ["GET", "POST"]
    allow_headers=["*"],           # 允许的 HTTP 请求头
)
@app.get("/")
async def root():
    return {"message": "Welcome to PaperAgent API"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.post("/register", response_model=schemas.UserResponse)
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

@app.post("/login", response_model=schemas.Token)
async def login(user_credentials: schemas.UserLogin, db: Session = Depends(get_db)):
    """用户登录接口"""
    user = crud.authenticate_user(db, user_credentials.email, user_credentials.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = auth.create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/me", response_model=schemas.UserResponse)
async def get_current_user_info(current_user_email: str = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    """获取当前用户信息"""
    user = crud.get_user_by_email(db, current_user_email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user

# 模板相关的API接口
@app.post("/templates", response_model=schemas.PaperTemplateResponse)
async def create_template(
    template: schemas.PaperTemplateCreate,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """创建论文模板"""
    user = crud.get_user_by_email(db, current_user_email)
    return crud.create_paper_template(db, template, user.id)

@app.get("/templates", response_model=List[schemas.PaperTemplateResponse])
async def get_user_templates(
    skip: int = 0,
    limit: int = 100,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """获取当前用户的模板"""
    user = crud.get_user_by_email(db, current_user_email)
    return crud.get_user_templates(db, user.id, skip, limit)

@app.get("/templates/public", response_model=List[schemas.PaperTemplateResponse])
async def get_public_templates(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """获取公开模板"""
    return crud.get_public_templates(db, skip, limit)

@app.get("/templates/{template_id}", response_model=schemas.PaperTemplateResponse)
async def get_template(
    template_id: int,
    current_user_email: str = Depends(auth.get_current_user),
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
    user = crud.get_user_by_email(db, current_user_email)
    if not template.is_public and template.created_by != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this template"
        )
    
    return template

@app.put("/templates/{template_id}", response_model=schemas.PaperTemplateResponse)
async def update_template(
    template_id: int,
    template_update: schemas.PaperTemplateUpdate,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """更新模板"""
    user = crud.get_user_by_email(db, current_user_email)
    return crud.update_paper_template(db, template_id, template_update, user.id)

@app.delete("/templates/{template_id}")
async def delete_template(
    template_id: int,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """删除模板"""
    user = crud.get_user_by_email(db, current_user_email)
    result = crud.delete_paper_template(db, template_id, user.id)
    
    # 删除模板文件
    template_file_service.delete_template_directory(template_id)
    
    return result

# 模板文件管理接口
@app.post("/templates/{template_id}/files")
async def upload_template_file(
    template_id: int,
    file: UploadFile = File(...),
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """上传模板文件"""
    # 检查模板权限
    template = crud.get_paper_template(db, template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    user = crud.get_user_by_email(db, current_user_email)
    if template.created_by != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this template"
        )
    
    # 读取文件内容
    content = await file.read()
    content_str = content.decode('utf-8')
    
    # 保存文件
    file_path = template_file_service.save_template_file(template_id, file.filename, content_str)
    
    return {"message": "File uploaded successfully", "file_path": file_path}

@app.get("/templates/{template_id}/files")
async def list_template_files(
    template_id: int,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """列出模板文件"""
    # 检查模板权限
    template = crud.get_paper_template(db, template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    user = crud.get_user_by_email(db, current_user_email)
    if not template.is_public and template.created_by != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this template"
        )
    
    files = template_file_service.list_template_files(template_id)
    return {"files": files}

@app.get("/templates/{template_id}/files/{filename}")
async def get_template_file(
    template_id: int,
    filename: str,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """获取模板文件内容"""
    # 检查模板权限
    template = crud.get_paper_template(db, template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    user = crud.get_user_by_email(db, current_user_email)
    if not template.is_public and template.created_by != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this template"
        )
    
    content = template_file_service.get_template_file_content(template_id, filename)
    return {"filename": filename, "content": content}

@app.delete("/templates/{template_id}/files/{filename}")
async def delete_template_file(
    template_id: int,
    filename: str,
    current_user_email: str = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """删除模板文件"""
    # 检查模板权限
    template = crud.get_paper_template(db, template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    user = crud.get_user_by_email(db, current_user_email)
    if template.created_by != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this template"
        )
    
    success = template_file_service.delete_template_file(template_id, filename)
    if success:
        return {"message": "File deleted successfully"}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)