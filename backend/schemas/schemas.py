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

# ModelConfig相关schemas
class ModelConfigBase(BaseModel):
    type: str  # brain(中枢大脑), code(代码实验), writing(论文写作)
    model_id: str
    base_url: str

class ModelConfigCreate(ModelConfigBase):
    api_key: str  # 创建时必须提供api_key

class ModelConfigUpdate(BaseModel):
    model_id: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None  # 更新时可以选择性提供api_key
    is_active: Optional[bool] = None

class ModelConfigResponse(ModelConfigBase):
    id: int
    is_active: bool
    created_at: datetime
    # 注意：响应中不包含api_key，确保安全性
    
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
