# 后端代码结构重构说明

## 重构目标
统一文件命名规范，消除命名混乱，提高代码可维护性。

## 命名规范
- 所有文件和文件夹使用**下划线命名法**（snake_case）
- 避免使用驼峰命名法（camelCase）
- 文件名简洁明了，表达核心功能

## 新的目录结构

### 1. AI系统模块 (`ai_system/`)
```
ai_system/
├── __init__.py                 # 主模块初始化
├── config/                     # 配置管理
│   ├── __init__.py
│   ├── async_config.py        # 异步配置
│   ├── environment.py         # 环境配置
│   └── logging_config.py      # 日志配置
├── core_agents/               # 核心代理
│   ├── __init__.py
│   ├── agent_base.py         # 代理基类（原agents.py）
│   └── main_agent.py         # 主代理
├── core_handlers/             # 核心处理器
│   ├── __init__.py
│   ├── llm_handler.py        # LLM处理器
│   └── llm_factory.py        # LLM工厂
├── core_managers/             # 核心管理器
│   ├── __init__.py
│   ├── stream_manager.py      # 流管理器
│   └── context_manager.py     # 上下文管理器
└── core_tools/                # 核心工具
    ├── __init__.py
    ├── code_executor.py       # 代码执行器
    ├── file_tools.py          # 文件工具
    └── template_tools.py      # 模板工具（原template_agent_tools.py）
```

### 2. 路由模块 (`routers/`)
```
routers/
├── __init__.py                # 主路由模块初始化
├── utils.py                   # 路由工具（原router_utils.py）
├── auth_routes/               # 认证路由
│   ├── __init__.py
│   └── auth.py               # 认证相关（原auth_router.py）
├── chat_routes/               # 聊天路由
│   ├── __init__.py
│   └── chat.py               # 聊天相关（原chat_router.py）
├── work_routes/               # 工作路由
│   ├── __init__.py
│   ├── work.py               # 工作管理（原work_router.py）
│   └── workspace.py          # 工作空间（原workspace_file_router.py）
├── file_routes/               # 文件路由
│   ├── __init__.py
│   ├── file.py               # 文件管理（原file_router.py）
│   └── template.py           # 模板管理（原template_router.py）
└── config_routes/             # 配置路由
    ├── __init__.py
    ├── model_config.py       # 模型配置（原model_config_router.py）
    └── context.py            # 上下文管理（原context_router.py）
```

### 3. 服务模块 (`services/`)
```
services/
├── __init__.py                # 主服务模块初始化
├── data_services/             # 数据服务
│   ├── __init__.py
│   ├── crud.py               # CRUD操作
│   └── utils.py              # 工具函数
├── file_services/             # 文件服务
│   ├── __init__.py
│   ├── file_helper.py        # 文件助手
│   ├── template_files.py     # 模板文件管理
│   └── workspace_files.py    # 工作空间文件管理
└── chat_services/             # 聊天服务
    ├── __init__.py
    ├── chat_service.py       # 聊天服务
    └── chat_history_manager.py # 聊天记录管理
```

### 4. 其他模块
```
├── auth/                      # 认证模块
│   ├── __init__.py
│   └── auth.py
├── database/                  # 数据库模块
│   ├── __init__.py
│   └── database.py
├── models/                    # 数据模型
│   ├── __init__.py
│   └── models.py
├── schemas/                   # 数据模式
│   ├── __init__.py
│   └── schemas.py
└── scripts/                   # 脚本工具
    └── migrate_chat_history.py
```

## 重构原则

### 1. 功能分组
- **core_agents**: 所有AI代理相关
- **core_handlers**: 所有处理器相关
- **core_managers**: 所有管理器相关
- **core_tools**: 所有工具相关

### 2. 命名统一
- 路由文件：`功能名.py`（如：`auth.py`, `chat.py`）
- 服务文件：`功能名_service.py`（如：`chat_service.py`）
- 工具文件：`功能名_tools.py`（如：`file_tools.py`）

### 3. 导入路径优化
- 使用相对导入，避免循环依赖
- 通过`__init__.py`文件统一导出接口
- 保持向后兼容性

## 使用说明

### 导入示例
```python
# 导入AI系统组件
from ai_system.core_agents import MainAgent
from ai_system.core_handlers import LLMFactory
from ai_system.core_tools import FileTools

# 导入服务
from services.data_services import crud
from services.file_services import FileHelper

# 导入路由
from routers.auth_routes import auth_router
from routers.chat_routes import chat_router
```

### 主应用注册
```python
from routers import all_routers

app = FastAPI()
for router in all_routers:
    app.include_router(router)
```

## 注意事项

1. **备份**: 重构前已备份到 `temp_restructure/` 目录
2. **测试**: 重构后需要全面测试所有功能
3. **文档**: 更新相关文档和注释
4. **部署**: 确保生产环境的平滑过渡

## 重构完成状态

- [x] 目录结构重组
- [x] 文件重命名
- [x] 导入路径更新
- [x] 模块初始化文件创建
- [x] 向后兼容性维护

重构完成！代码结构现在更加清晰、规范，便于维护和扩展。
