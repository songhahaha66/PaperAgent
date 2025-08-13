# PaperAgent 用户认证系统

## 概述

本系统实现了完整的用户认证功能，包括用户注册、登录、JWT Token管理和权限控制。

## 功能特性

- ✅ 用户注册接口 (`/register`)
- ✅ 用户登录接口 (`/login`) 
- ✅ JWT Token 管理
- ✅ 密码加密存储 (bcrypt)
- ✅ 用户权限控制
- ✅ 获取当前用户信息 (`/me`)

## API 接口

### 1. 用户注册

**POST** `/register`

请求体:
```json
{
  "email": "user@example.com",
  "username": "username",
  "password": "password123"
}
```

响应:
```json
{
  "id": 1,
  "email": "user@example.com",
  "username": "username",
  "created_at": "2024-01-01T00:00:00",
  "is_active": true
}
```

### 2. 用户登录

**POST** `/login`

请求体:
```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

响应:
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "token_type": "bearer"
}
```

### 3. 获取当前用户信息

**GET** `/me`

请求头:
```
Authorization: Bearer <access_token>
```

响应:
```json
{
  "id": 1,
  "email": "user@example.com",
  "username": "username",
  "created_at": "2024-01-01T00:00:00",
  "is_active": true
}
```

## 环境配置

复制 `env_example` 为 `.env` 并配置以下参数:

```bash
# 数据库配置
DATABASE_URL=postgresql://username:password@localhost:5432/paperagent

# JWT配置
SECRET_KEY=your-secret-key-here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# 应用配置
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
```

## 运行方式

1. 安装依赖:
```bash
uv sync
```

2. 启动服务:
```bash
python main.py
```

3. 访问API文档:
```
http://localhost:8000/docs
```

## 安全特性

- 密码使用 bcrypt 加密存储
- JWT Token 过期时间可配置
- 用户名和邮箱唯一性验证
- 输入参数验证和清理

## 数据库

系统会自动创建 `users` 表，包含以下字段:
- id: 主键
- username: 用户名 (唯一)
- email: 邮箱 (唯一)  
- password_hash: 加密后的密码
- created_at: 创建时间
- updated_at: 更新时间
- is_active: 账户状态
