from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from models import models
from database.database import engine
from routers import all_routers
import uvicorn
import logging

# 导入日志配置
from ai_system.config.logging_config import setup_simple_logging
# 导入异步配置管理器
from ai_system.config.async_config import initialize_async_config, shutdown_async_config
from config.paths import get_project_root, get_pa_data_base, get_workspaces_path, get_templates_path

# 设置日志系统（只显示在终端，关闭LiteLLM日志）
setup_simple_logging("INFO")

# 创建数据库表
models.Base.metadata.create_all(bind=engine)

# 初始化异步配置
initialize_async_config(max_workers=20, max_processes=4)

# 创建logger
logger = logging.getLogger(__name__)

# 定义lifespan事件处理器
async def lifespan(app: FastAPI):
    """应用生命周期事件处理器"""
    # 启动时执行
    logger.info("应用启动中...")
    
    # 显示路径配置
    logger.info(f"项目根目录: {get_project_root()}")
    logger.info(f"PA_DATA 目录: {get_pa_data_base()}")
    logger.info(f"工作空间目录: {get_workspaces_path()}")
    logger.info(f"模板目录: {get_templates_path()}")
    
    yield
    # 关闭时执行
    logger.info("正在关闭应用...")
    shutdown_async_config()
    logger.info("异步配置已关闭")

app = FastAPI(
    title="PaperAgent API",
    description="API for PaperAgent - an AI-powered paper generation system",
    version="0.1.0",
    # 优化异步设置
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    # 使用现代lifespan事件处理器
    lifespan=lifespan
)

# CORS配置
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

# 添加可信主机中间件（可选，用于生产环境）
# app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

# 基础路由
@app.get("/")
async def root():
    return {"message": "Welcome to PaperAgent API"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/async-status")
async def async_status():
    """获取异步配置状态"""
    from ai_system.config.async_config import get_async_config
    return get_async_config().get_status()

# 注册路由模块（统一）
for router in all_routers:
    app.include_router(router)

if __name__ == "__main__":
    # 优化uvicorn配置，提高并发性能
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        # 异步优化配置
        loop="asyncio",
        # 增加工作进程数量（如果需要）
        # workers=4,
        # 优化异步设置
        access_log=True,
        # 增加连接限制
        limit_concurrency=1000,
        limit_max_requests=10000,
        # 超时设置
        timeout_keep_alive=30,
        # 日志级别
        log_level="info"
    )