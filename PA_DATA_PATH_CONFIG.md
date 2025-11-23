# PA_DATA 路径配置说明

## 问题描述

之前版本中，`pa_data` 路径使用相对路径 `../pa_data`，这会导致以下问题：

1. 在不同的启动位置（项目根目录 vs backend 目录）会导致路径不一致
2. Docker 容器内的路径与本地开发环境不匹配
3. 数据持久化不稳定，可能在错误的位置创建 pa_data 目录

## 解决方案

### 1. 统一的路径配置模块

创建了 `backend/config/paths.py` 模块，提供统一的路径管理：

```python
from config.paths import (
    get_project_root,      # 获取项目根目录
    get_pa_data_base,      # 获取 pa_data 基础目录
    get_workspaces_path,   # 获取工作空间目录
    get_templates_path,    # 获取模板目录
    get_workspace_path,    # 获取指定工作的工作空间路径
)
```

### 2. 路径解析逻辑

- **环境变量优先**: 如果设置了 `PA_DATA_PATH` 环境变量，将使用该路径
- **自动检测**: 否则自动检测项目根目录，在其下创建 `pa_data` 目录
- **Docker 兼容**: 在容器内也能正确工作

### 3. 环境变量配置

在 `.env` 文件中可以设置（可选）：

```bash
# 自定义 pa_data 路径
PA_DATA_PATH=/path/to/pa_data
```

如果不设置，系统会使用默认位置：
- **本地开发**: `<项目根>/pa_data`
- **Docker 容器**: 通过 volume 挂载到 `/app/pa_data`

## 目录结构

```
pa_data/
├── workspaces/          # 工作空间目录
│   └── <work_id>/       # 每个工作对应一个目录
│       ├── attachment/  # 附件目录
│       ├── code/        # 代码文件
│       ├── logs/        # 日志文件
│       ├── outputs/     # 输出文件
│       ├── temp/        # 临时文件
│       ├── paper.md     # 生成的论文
│       ├── metadata.json        # 元数据
│       └── chat_history.json    # 对话历史
└── templates/           # 模板目录
    └── <template_id>_*.md  # 模板文件
```

## 迁移说明

### 1. 本地开发环境

如果你之前在 `backend/pa_data` 下有数据，需要移动到项目根目录：

```bash
# 从项目根目录执行
mv backend/pa_data ./pa_data
```

### 2. Docker 环境

docker-compose.yml 已配置好 volume 挂载：

```yaml
volumes:
  - ./pa_data:/app/pa_data  # 挂载到容器内的 /app/pa_data
```

### 3. 自定义路径

如果想使用自定义路径（例如外部存储），设置环境变量：

```bash
# .env 文件
PA_DATA_PATH=/mnt/storage/paperagent_data
```

## 受影响的模块

以下模块已更新使用统一的路径配置：

1. `backend/services/file_services/workspace_files.py` - 工作空间文件服务
2. `backend/services/file_services/template_files.py` - 模板文件服务
3. `backend/services/chat_services/chat_history_manager.py` - 聊天记录管理
4. `backend/services/data_services/crud.py` - CRUD 操作
5. `backend/ai_system/config/environment.py` - AI 环境配置
6. `backend/routers/work_routes/work.py` - 工作路由

## 验证

启动应用后，查看日志输出，确认路径配置正确：

```
INFO:     项目根目录: /path/to/PaperAgent
INFO:     PA_DATA 目录: /path/to/PaperAgent/pa_data
INFO:     工作空间目录: /path/to/PaperAgent/pa_data/workspaces
INFO:     模板目录: /path/to/PaperAgent/pa_data/templates
```

## 测试

可以通过以下方式测试路径配置：

```python
# 在 Python shell 中
from config.paths import get_pa_data_base, get_workspaces_path
print(f"PA_DATA: {get_pa_data_base()}")
print(f"Workspaces: {get_workspaces_path()}")
```

## 注意事项

1. **权限**: 确保运行用户对 pa_data 目录有读写权限
2. **备份**: 在迁移数据前建议先备份
3. **Docker**: 容器重建时，volume 数据会保留
4. **开发**: 本地开发时建议在项目根目录启动应用
