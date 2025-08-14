from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from models import models
from database.database import engine
from routers import auth_router, template_router, file_router

# 创建数据库表
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="PaperAgent API",
    description="API for PaperAgent - an AI-powered paper generation system",
    version="0.1.0"
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

# 基础路由
@app.get("/")
async def root():
    return {"message": "Welcome to PaperAgent API"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# 注册路由模块
app.include_router(auth_router.router)
app.include_router(template_router.router)
app.include_router(file_router.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)