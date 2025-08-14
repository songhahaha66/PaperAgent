from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class UserBase(BaseModel):
    email: EmailStr
    username: str

class UserCreate(UserBase):
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(UserBase):
    id: int
    created_at: datetime
    is_active: bool
    
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

class SystemConfigResponse(BaseModel):
    id: int
    is_allow_register: bool
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class PaperTemplateBase(BaseModel):
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    file_path: str  # 模板文件路径
    is_public: bool = False

class PaperTemplateCreate(PaperTemplateBase):
    pass

class PaperTemplateCreateWithContent(BaseModel):
    """创建模板时包含文件内容的schema"""
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    file_path: str  # 模板文件路径
    is_public: bool = False
    content: str = ""  # 模板文件内容

class PaperTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    file_path: Optional[str] = None  # 允许更新文件路径
    is_public: Optional[bool] = None

class PaperTemplateResponse(PaperTemplateBase):
    id: int
    created_at: datetime
    updated_at: datetime
    created_by: int
    
    class Config:
        from_attributes = True
