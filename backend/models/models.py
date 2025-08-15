from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # 关联关系
    creator = relationship("User", back_populates="works")

# 添加反向关系
User.works = relationship("Work", back_populates="creator")
