# 后端开发规范

## 系统架构

- **包管理工具**: [uv](https://github.com/astral-sh/uv) - 极快的 Python 包安装器和解析器
- **API 框架**: [FastAPI](https://fastapi.tiangolo.com/) - 现代、快速（高性能）的 Web 框架
- **数据库**: [PostgreSQL](https://www.postgresql.org/) - 强大的开源对象关系数据库系统

## 开发原则

**尽量避免所谓的回退写法，不必考虑太多情况，这会使代码过于臃肿。**

保持代码简洁，专注于当前需求，不要添加与当前功能无关的属性或方法。

## 配置文件

- `.env.example` - 示例配置文件，包含示例配置项
- `.env` - 实际配置文件，包含真实的配置值

## 数据库

数据库设计文件位于 [.lingma/rules/backend/DATABASE_DESIGN.md](DATABASE_DESIGN.md)，包含所有表结构和关系定义。

## 项目结构

后端项目结构遵循功能模块化原则：

```
backend/
├── main.py              # 应用入口点
├── auth/                # 认证模块
├── database/            # 数据库连接和会话管理
├── models/              # 数据模型定义
├── schemas/             # 数据验证模式
├── services/            # 业务逻辑实现
├── docs/                # 文档文件
└── ...
```

## API 设计

遵循 RESTful API 设计原则：

1. 使用名词而非动词表示资源
2. 使用 HTTP 方法表示操作类型
3. 合理使用 HTTP 状态码
4. 返回统一的 JSON 格式响应
5. 使用 Pydantic 进行请求和响应数据验证