# PaperAgent 后端

## 项目结构

```
backend/
├── main.py              # 主应用入口文件
├── auth/                # 认证相关模块
│   ├── __init__.py
│   └── auth.py         # JWT认证、用户验证等
├── core/                # 核心功能模块
│   └── __init__.py     # 核心配置、中间件等
├── database/            # 数据库相关
│   ├── __init__.py
│   └── database.py     # 数据库连接、会话管理
├── models/              # 数据模型
│   ├── __init__.py
│   └── models.py       # SQLAlchemy ORM模型
├── schemas/             # 数据验证模式
│   ├── __init__.py
│   └── schemas.py      # Pydantic数据验证模式
├── services/            # 业务逻辑服务
│   ├── __init__.py
│   └── crud.py         # 数据库CRUD操作
├── tasks/               # 任务调度和队列
│   └── __init__.py     # 异步任务、队列管理
├── utils/               # 工具函数
│   └── __init__.py     # 通用工具函数
├── docs/                # 文档
├── pyproject.toml       # 项目配置
└── uv.lock             # 依赖锁定文件
```

## 技术栈

- **框架**: FastAPI
- **数据库**: PostgreSQL + SQLAlchemy
- **认证**: JWT
- **包管理**: uv
- **异步**: asyncio

## 开发说明

- 保持代码简洁，避免过度设计
- 按功能模块组织代码，便于维护
- 遵循FastAPI最佳实践