from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text, ForeignKey, JSON
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
    provider = Column(String(50), nullable=False, server_default="openai")  # AI提供商：openai, anthropic, google, local
    model_id = Column(String(50), nullable=False)  # 模型ID
    base_url = Column(String(100), nullable=False)  # 模型URL
    timeout_seconds = Column(Float, nullable=True)  # None uses the model client default (600s).
    api_key = Column(String(255), nullable=False)  # API密钥
    is_active = Column(Boolean, default=True)  # 是否激活
    created_at = Column(DateTime(timezone=True), server_default=func.now())  # 创建时间
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)  # 添加用户关联

    # 关联关系
    creator = relationship("User", back_populates="model_configs")

class PaperTemplate(Base):
    __tablename__ = "paper_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    category = Column(String(50))
    output_format = Column(String(10), nullable=False, default="markdown")  # 输出格式：md, word, latex
    file_path = Column(String(500), nullable=False)  # 模板文件路径
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_public = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # 关联关系
    creator = relationship("User", back_populates="templates")

# 添加反向关系
User.templates = relationship("PaperTemplate", back_populates="creator")
User.model_configs = relationship("ModelConfig", back_populates="creator")

class Project(Base):
    __tablename__ = "projects"
    
    id = Column(Integer, primary_key=True, index=True)
    architecture_version = Column(Integer, nullable=False, default=2, server_default="1")
    work_id = Column(String(50), unique=True, nullable=False, index=True)  # 唯一工作ID
    title = Column(String(200), nullable=False)  # 工作标题
    description = Column(Text)  # 工作描述
    status = Column(String(50), nullable=False, default="created")  # 工作状态
    tags = Column(Text)  # 标签，JSON格式存储
    template_id = Column(Integer, ForeignKey("paper_templates.id"), nullable=True)  # 关联的论文模板ID
    output_mode = Column(String(20), nullable=False, default="markdown")  # 输出模式：markdown, word, latex
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # 关联关系
    creator = relationship("User", back_populates="projects")
    template = relationship("PaperTemplate")  # 关联论文模板

# 添加反向关系
User.projects = relationship("Project", back_populates="creator")

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
    # 删除messages关联关系，聊天记录改用JSON文件存储

# 删除ChatMessage表，聊天记录改用JSON文件存储

# 更新User模型的反向关系
User.chat_sessions = relationship("ChatSession", back_populates="creator")


class WebAuthnCredential(Base):
    """用户绑定的 Passkey / WebAuthn 凭证"""
    __tablename__ = "webauthn_credentials"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    credential_id = Column(String(1024), unique=True, nullable=False, index=True)
    public_key = Column(Text, nullable=False)
    sign_count = Column(Integer, nullable=False, default=0)
    device_type = Column(String(32), nullable=True)
    backed_up = Column(Boolean, default=False)
    transports = Column(JSON, nullable=True)
    aaguid = Column(String(64), nullable=True)
    name = Column(String(100), nullable=False, default="Passkey")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="webauthn_credentials")


class WebAuthnChallenge(Base):
    """一次性 WebAuthn challenge，注册/登录校验后作废"""
    __tablename__ = "webauthn_challenges"

    id = Column(Integer, primary_key=True, index=True)
    challenge_id = Column(String(64), unique=True, nullable=False, index=True)
    challenge = Column(String(255), nullable=False)
    challenge_type = Column(String(32), nullable=False)  # registration | authentication
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


User.webauthn_credentials = relationship("WebAuthnCredential", back_populates="user")


class ResearchExperience(Base):
    """User-owned, versioned methods with an explicit adoption lifecycle."""
    __tablename__ = 'research_experiences'
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    source_work_id = Column(String(50), nullable=False, index=True)
    source_key = Column(String(255), nullable=False, unique=True)
    version = Column(Integer, nullable=False, default=1)
    content_hash = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    deleted = Column(Boolean, nullable=False, default=False)
    status = Column(String(20), nullable=False, default='candidate')
    history = Column(JSON, nullable=False, default=list)
    validation = Column(JSON, nullable=True)


class ResearchExperiencePreference(Base):
    __tablename__ = 'research_experience_preferences'
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
