from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

# 同步数据库引擎和会话（保持兼容性）
# pool_pre_ping: 使用前检测连接是否有效，避免使用已断开的连接
# pool_recycle: 连接回收时间（秒），防止数据库服务器端超时断开
# pool_size: 连接池大小
# max_overflow: 允许超出pool_size的连接数
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,  # 5分钟回收一次连接
    pool_size=10,
    max_overflow=20
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """同步数据库会话依赖注入"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
