from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Literal, Optional, List
from datetime import datetime
from ai_system.config.generation import GenerationSettings

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


class PasskeyLoginOptionsRequest(BaseModel):
    email: Optional[EmailStr] = None


class PasskeyOptionsResponse(BaseModel):
    challenge_id: str
    options: dict


class PasskeyRegisterVerifyRequest(BaseModel):
    challenge_id: str
    credential: dict
    name: Optional[str] = None


class PasskeyLoginVerifyRequest(BaseModel):
    challenge_id: str
    credential: dict


class PasskeyCredentialResponse(BaseModel):
    id: int
    name: str
    device_type: Optional[str] = None
    backed_up: bool = False
    transports: Optional[List[str]] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PasskeyUpdateRequest(BaseModel):
    name: str


# ModelConfig相关schemas
class ModelConfigBase(BaseModel):
    type: Literal["brain", "code", "writing"]
    model_id: str
    base_url: str

    timeout_seconds: Optional[float] = Field(default=None,gt=0,le=3600)
    thinking_enabled: bool = False
    generation_settings: GenerationSettings = Field(default_factory=GenerationSettings)

class ModelConfigCreate(ModelConfigBase):
    api_key: str  # 创建时必须提供api_key

class ModelConfigUpdate(BaseModel):
    thinking_enabled: bool = False
    generation_settings: GenerationSettings = Field(default_factory=GenerationSettings)
    timeout_seconds: Optional[float] = Field(default=None,gt=0,le=3600)
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
    output_format: str = "markdown"  # 输出格式：md, word, latex
    file_path: str  # 模板文件路径
    is_public: bool = False

class PaperTemplateCreate(PaperTemplateBase):
    pass

class PaperTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    is_public: Optional[bool] = None

class PaperTemplateResponse(PaperTemplateBase):
    id: int
    created_at: datetime
    updated_at: datetime
    created_by: int

    class Config:
        from_attributes = True


class TemplateAnalysisResponse(BaseModel):
    template_id: int
    status: str
    contract: str = ""
    image_count: int = 0
    has_style_profile: bool = False
    analyzed_at: Optional[str] = None
    error: Optional[str] = None

# Work相关schemas
class WorkBase(BaseModel):
    title: str
    description: Optional[str] = None
    tags: Optional[str] = None
    template_id: Optional[int] = None  # 关联的论文模板ID

class WorkCreate(WorkBase):
    output_mode: str = "markdown"  # 输出模式：markdown, word, latex

class WorkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    template_id: Optional[int] = None  # 关联的论文模板ID
    output_mode: Optional[str] = None  # 输出模式：markdown, word, latex

class WorkResponse(WorkBase):
    architecture_version: int = 1
    execution_reason: Optional[dict] = None
    id: int
    work_id: str
    status: str
    progress: int = 0
    template_id: Optional[int] = None  # 关联的论文模板ID
    output_mode: str = "markdown"  # 输出模式：markdown, word, latex
    created_at: datetime
    updated_at: datetime
    created_by: int

    class Config:
        from_attributes = True

class WorkListResponse(BaseModel):
    works: list[WorkResponse]
    total: int
    page: int
    size: int


# 简化后的聊天系统相关schemas


# JSON格式的聊天记录响应（从JSON文件读取）


# 附件相关schemas
class AttachmentInfo(BaseModel):
    filename: str           # 存储的文件名
    original_filename: str  # 原始文件名
    file_type: str         # 文件类型
    file_size: int         # 文件大小
    mime_type: str         # MIME类型
    upload_time: str       # 上传时间

class AttachmentListResponse(BaseModel):
    attachments: List[AttachmentInfo]
    total: int

class AttachmentUploadResponse(BaseModel):
    message: str
    attachment: AttachmentInfo
