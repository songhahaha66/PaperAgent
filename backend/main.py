from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
import models, schemas, crud, auth, database
from database import engine, get_db
from fastapi.middleware.cors import CORSMiddleware
# 创建数据库表
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="PaperAgent API",
    description="API for PaperAgent - an AI-powered paper generation system",
    version="0.1.0"
)
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
@app.get("/")
async def root():
    return {"message": "Welcome to PaperAgent API"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.post("/register", response_model=schemas.UserResponse)
async def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    """用户注册接口"""
    return crud.create_user(db=db, user=user)

@app.post("/login", response_model=schemas.Token)
async def login(user_credentials: schemas.UserLogin, db: Session = Depends(get_db)):
    """用户登录接口"""
    user = crud.authenticate_user(db, user_credentials.email, user_credentials.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = auth.create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/me", response_model=schemas.UserResponse)
async def get_current_user_info(current_user_email: str = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    """获取当前用户信息"""
    user = crud.get_user_by_email(db, current_user_email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)