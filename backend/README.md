# PaperAgent 后端

PaperAgent 后端基于 FastAPI 构建，提供 RESTful API 服务，处理用户认证、论文生成任务、文件管理等功能。

## 项目结构

```
backend/
├── main.py              # 主应用入口文件
├── auth/                # 认证相关模块
│   ├── __init__.py
│   └── auth.py         # JWT 认证、用户验证等
├── database/            # 数据库相关
│   ├── __init__.py
│   └── database.py     # 数据库连接、会话管理
├── models/              # 数据模型
│   ├── __init__.py
│   └── models.py       # SQLAlchemy ORM 模型
├── schemas/             # 数据验证模式
│   ├── __init__.py
│   └── schemas.py      # Pydantic 数据验证模式
├── services/            # 业务逻辑服务
│   ├── __init__.py
│   ├── crud.py         # 数据库 CRUD 操作
│   └── template_files.py # 模板文件处理
├── docs/                # 后端相关文档
├── pyproject.toml       # 项目配置
└── uv.lock             # 依赖锁定文件
```

## 技术栈

- **框架**: [FastAPI](https://fastapi.tiangolo.com/) - 现代、快速（高性能）的 Web 框架
- **数据库**: [PostgreSQL](https://www.postgresql.org/) + [SQLAlchemy](https://www.sqlalchemy.org/) ORM
- **认证**: [JWT](https://jwt.io/) (JSON Web Tokens)
- **包管理**: [uv](https://github.com/astral-sh/uv) - 极快的 Python 包安装器和解析器
- **异步**: [asyncio](https://docs.python.org/3/library/asyncio.html) Python 异步 I/O

## 核心功能模块

### 认证系统
- 用户注册和登录
- JWT Token 生成和验证
- 密码加密存储（bcrypt）

### 数据库管理
- 用户信息管理
- 工作历史记录
- 消息存储
- 论文模板管理
- AI 模型配置

### 文件服务
- LaTeX 文件管理
- 代码文件管理
- 数据文件管理
- 文件上传下载接口

### 论文模板系统
- 模板库管理
- 模板应用接口
- 格式转换服务
- LaTeX 编译服务

## 开发说明

- 保持代码简洁，避免过度设计
- 按功能模块组织代码，便于维护
- 遵循 [FastAPI 最佳实践](https://fastapi.tiangolo.com/tutorial/)
- 使用 Pydantic 进行数据验证
- 使用 SQLAlchemy 进行数据库操作