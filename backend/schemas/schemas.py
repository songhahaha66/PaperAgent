from pydantic import BaseModel, EmailStr
from typing import Optional, List
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

# Work相关schemas
class WorkBase(BaseModel):
    title: str
    description: Optional[str] = None
    tags: Optional[str] = None
    template_id: Optional[int] = None  # 关联的论文模板ID

class WorkCreate(WorkBase):
    pass

class WorkUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[int] = None
    tags: Optional[str] = None
    template_id: Optional[str] = None  # 关联的论文模板ID

class WorkResponse(WorkBase):
    id: int
    work_id: str
    status: str
    progress: int
    template_id: Optional[int] = None  # 关联的论文模板ID
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


# 聊天系统相关schemas
class ChatMessageBase(BaseModel):
    role: str  # user, assistant, system, tool
    content: str
    timestamp: Optional[datetime] = None
    tool_calls: Optional[List[dict]] = None
    tool_results: Optional[List[dict]] = None

class ChatMessageCreate(ChatMessageBase):
    pass

class ChatSessionBase(BaseModel):
    session_id: str
    work_id: str
    system_type: str  # brain, code, writing
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class ChatSessionCreate(ChatSessionBase):
    pass

# --- 新增聊天相关schemas ---

class ChatSessionCreateRequest(BaseModel):
    """创建聊天会话请求"""
    work_id: str
    system_type: str  # brain, code, writing
    title: Optional[str] = None

class ChatSessionResponse(BaseModel):
    """聊天会话响应"""
    id: int
    session_id: str
    work_id: str
    system_type: str
    title: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime
    created_by: int
    total_messages: int = 0
    
    class Config:
        from_attributes = True

class ChatMessageCreateRequest(BaseModel):
    """创建聊天消息请求"""
    role: str  # user, assistant, system, tool
    content: str
    tool_calls: Optional[dict] = None
    tool_results: Optional[dict] = None
    message_metadata: Optional[dict] = None

class ChatMessageResponse(BaseModel):
    """聊天消息响应"""
    id: int
    session_id: str
    role: str
    content: str
    tool_calls: Optional[dict] = None
    tool_results: Optional[dict] = None
    message_metadata: Optional[dict] = None
    created_at: datetime
    
    class Config:
        from_attributes = True

class ChatStreamRequest(BaseModel):
    """流式聊天请求"""
    problem: str
    model: Optional[str] = None

class WorkFlowStateCreateRequest(BaseModel):
    """创建工作流状态请求"""
    work_id: str
    current_state: str
    previous_state: Optional[str] = None
    state_data: Optional[dict] = None
    transition_reason: Optional[str] = None

class WorkFlowStateResponse(BaseModel):
    """工作流状态响应"""
    id: int
    work_id: str
    current_state: str
    previous_state: Optional[str]
    state_data: Optional[dict]
    transition_reason: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True
