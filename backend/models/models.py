from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_active = Column(Boolean, default=True)

class SystemConfig(Base):
    __tablename__ = "system_config"
    
    id = Column(Integer, primary_key=True, index=True)
    is_allow_register = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class ModelConfig(Base):
    __tablename__ = "model_configs"
    
    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(50), nullable=False)  # 模型种类：brain(中枢大脑), code(代码实验), writing(论文写作)
    model_id = Column(String(50), nullable=False)  # 模型ID
    base_url = Column(String(100), nullable=False)  # 模型URL
    api_key = Column(String(255), nullable=False)  # API密钥
    is_active = Column(Boolean, default=True)  # 是否激活
    created_at = Column(DateTime(timezone=True), server_default=func.now())  # 创建时间

class PaperTemplate(Base):
    __tablename__ = "paper_templates"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    category = Column(String(50))
    file_path = Column(String(500), nullable=False)  # 模板文件路径
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_public = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # 关联关系
    creator = relationship("User", back_populates="templates")

# 添加反向关系
User.templates = relationship("PaperTemplate", back_populates="creator")

class Work(Base):
    __tablename__ = "works"
    
    id = Column(Integer, primary_key=True, index=True)
    work_id = Column(String(50), unique=True, nullable=False, index=True)  # 唯一工作ID
    title = Column(String(200), nullable=False)  # 工作标题
    description = Column(Text)  # 工作描述
    status = Column(String(50), nullable=False, default="created")  # 工作状态
    progress = Column(Integer, default=0)  # 进度百分比 (0-100)
    tags = Column(Text)  # 标签，JSON格式存储
    template_id = Column(Integer, ForeignKey("paper_templates.id"), nullable=True)  # 关联的论文模板ID
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # 关联关系
    creator = relationship("User", back_populates="works")
    template = relationship("PaperTemplate")  # 关联论文模板

# 添加反向关系
User.works = relationship("Work", back_populates="creator")

# --- 新增聊天相关表 ---

class ChatSession(Base):
    """聊天会话表"""
    __tablename__ = "chat_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), unique=True, nullable=False, index=True)  # 唯一会话ID
    work_id = Column(String(50), nullable=False, index=True)  # 关联的工作ID
    system_type = Column(String(20), nullable=False)  # 系统类型：brain(中枢大脑), code(代码实验), writing(论文写作)
    title = Column(String(200))  # 会话标题
    status = Column(String(20), default="active")  # 会话状态：active, archived, deleted
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # 关联关系
    creator = relationship("User", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")

class ChatMessage(Base):
    """聊天消息表"""
    __tablename__ = "chat_messages"
    
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), ForeignKey("chat_sessions.session_id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # 消息角色：user, assistant, system, tool
    content = Column(Text, nullable=False)  # 消息内容
    tool_calls = Column(JSON)  # 工具调用信息，JSON格式
    tool_results = Column(JSON)  # 工具执行结果，JSON格式
    message_metadata = Column(JSON)  # 额外元数据，如流式传输状态等
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关联关系
    session = relationship("ChatSession", back_populates="messages")

class WorkFlowState(Base):
    """工作流状态表"""
    __tablename__ = "work_flow_states"
    
    id = Column(Integer, primary_key=True, index=True)
    work_id = Column(String(50), nullable=False, index=True)  # 关联的工作ID
    current_state = Column(String(50), nullable=False)  # 当前状态
    previous_state = Column(String(50))  # 前一个状态
    state_data = Column(JSON)  # 状态相关的数据
    transition_reason = Column(Text)  # 状态转换原因
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 状态枚举值
    # CREATED, PLANNING, MODELING, CODING, EXECUTING, ANALYZING, WRITING, REVIEWING, COMPLETED, ARCHIVED

# 更新User模型的反向关系
User.chat_sessions = relationship("ChatSession", back_populates="creator")
