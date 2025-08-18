# 工作空间管理系统

## 概述

工作空间管理系统是PaperAgent的核心组件，负责管理每个论文生成任务的完整生命周期。每个工作对应一个唯一的工作ID，在对应的文件夹下进行代码生成、执行、论文写作等操作。

## 系统架构

### 核心概念

- **工作(Work)**: 每个论文生成任务对应一个唯一ID
- **工作空间(Workspace)**: 每个工作ID对应的文件夹，包含所有相关文件
- **工作状态**: 跟踪工作的当前状态和进度

### 文件夹结构

```
workspaces/
├── {work_id}/
│   ├── metadata.json          # 工作元数据
│   ├── chat_history.json      # 对话历史
│   ├── user_inputs/           # 用户初始输入和附件
│   │   ├── init.md            # 用户初始需求描述
│   │   ├── attachments/       # 用户上传的附件
│   │   └── requirements.txt   # 用户需求规格说明
│   ├── generated_code/        # 生成的代码文件
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── data/
│   ├── paper_drafts/         # 论文草稿
│   │   ├── outline.md
│   │   ├── sections/
│   │   └── final_paper.md
│   └── resources/            # 相关资源
│       ├── references/
│       └── images/
```

## API接口

### 工作管理API

#### 创建工作
```http
POST /api/works
Content-Type: application/json

{
  "title": "计算100平方的家庭使用空调降温速率研究",
  "description": "通过数学模型和数值模拟分析空调降温速率",
  "tags": "热力学,数值模拟,空调"
}
```

#### 获取工作列表
```http
GET /api/works?skip=0&limit=10&status=in_progress&search=空调
```

#### 获取工作详情
```http
GET /api/works/{work_id}
```

#### 更新工作信息
```http
PUT /api/works/{work_id}
Content-Type: application/json

{
  "title": "更新后的标题",
  "description": "更新后的描述",
  "status": "in_progress",
  "progress": 50
}
```

#### 更新工作状态
```http
PATCH /api/works/{work_id}/status?status=in_progress&progress=50
```

#### 删除工作
```http
DELETE /api/works/{work_id}
```

#### 获取工作元数据
```http
GET /api/works/{work_id}/metadata
```

#### 获取对话历史
```http
GET /api/works/{work_id}/chat-history
```

### 工作空间文件管理API

#### 列出文件
```http
GET /api/workspace/{work_id}/files?path=generated_code
```

#### 读取文件
```http
GET /api/workspace/{work_id}/files/{file_path}
```

#### 写入文件
```http
POST /api/workspace/{work_id}/files/{file_path}
Content-Type: application/x-www-form-urlencoded

content=文件内容
```

#### 上传文件
```http
POST /api/workspace/{work_id}/upload
Content-Type: multipart/form-data

file_path: generated_code/main.py
file: [文件内容]
```

#### 删除文件
```http
DELETE /api/workspace/{work_id}/files/{file_path}
```

#### 创建目录
```http
POST /api/workspace/{work_id}/mkdir
Content-Type: application/x-www-form-urlencoded

dir_path: new_folder
```

#### 获取文件信息
```http
GET /api/workspace/{work_id}/files/{file_path}/info
```

## 数据模型

### Work模型

```python
class Work(Base):
    __tablename__ = "works"
    
    id = Column(Integer, primary_key=True, index=True)
    work_id = Column(String(50), unique=True, nullable=False, index=True)  # 唯一工作ID
    title = Column(String(200), nullable=False)  # 工作标题
    description = Column(Text)  # 工作描述
    status = Column(String(50), nullable=False, default="created")  # 工作状态
    progress = Column(Integer, default=0)  # 进度百分比 (0-100)
    tags = Column(Text)  # 标签，JSON格式存储
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
```

### 工作状态枚举

- `created`: 已创建
- `in_progress`: 进行中
- `completed`: 已完成
- `paused`: 已暂停
- `cancelled`: 已取消

## 权限控制

- 只有工作的创建者可以查看、修改、删除工作
- 工作空间文件操作需要验证用户权限
- 支持公开和私有工作空间

## 错误处理

系统提供详细的错误信息和HTTP状态码：

- `400 Bad Request`: 请求参数错误
- `401 Unauthorized`: 未认证
- `403 Forbidden`: 权限不足
- `404 Not Found`: 资源不存在
- `500 Internal Server Error`: 服务器内部错误

## 使用示例

### 创建工作流程

1. 用户创建新工作
2. 系统生成唯一工作ID
3. 自动创建工作空间文件夹结构
4. 初始化元数据和对话历史文件
5. 返回工作信息

### 文件操作流程

1. 验证用户权限
2. 检查工作空间是否存在
3. 执行文件操作（读取、写入、删除等）
4. 返回操作结果

## 测试

运行测试脚本验证系统功能：

```bash
cd backend
python test_workspace.py
```

## 注意事项

1. 工作空间文件夹路径为 `../pa_data/workspaces/{work_id}`
2. 文件大小限制：读取文件最大10MB，上传文件最大50MB
3. 删除工作时会同时删除整个工作空间文件夹
4. 支持嵌套目录结构
5. 自动创建必要的父目录

## 扩展功能

- 支持文件版本控制
- 支持文件类型识别和预览
- 支持批量文件操作
- 支持文件同步和备份
- 支持工作空间模板
