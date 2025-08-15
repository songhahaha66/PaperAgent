# AI论文生成功能架构规划

## 1. 功能概述

基于现有的前端设计，AI论文生成功能将实现一个完整的工作流系统，每个工作对应一个唯一ID，在对应文件夹下进行代码生成、执行、论文写作等操作。

## 2. 系统架构设计

### 2.1 核心概念

- **工作(Work)**: 每个论文生成任务对应一个唯一ID
- **工作空间(Workspace)**: 每个工作ID对应的文件夹，包含所有相关文件
- **多系统协作**: 中枢系统、代码执行系统、论文生成系统协同工作

### 2.2 系统组件

```
PaperAgent
├── 前端 (Vue3 + TDesign)
├── 后端 (FastAPI)
├── 工作空间管理
├── AI模型集成
└── 文件存储系统
```

## 3. 工作空间结构设计

### 3.1 文件夹结构（文件夹在../pa_data下）

```
workspaces/
├── {work_id}/
│   ├── metadata.json          # 工作元数据
│   ├── chat_history.json      # 对话历史
│   ├── generated_code/        # 生成的代码文件
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── data/
│   ├── execution_results/     # 代码执行结果
│   │   ├── output.log
│   │   ├── plots/
│   │   └── data_output/
│   ├── paper_drafts/         # 论文草稿
│   │   ├── outline.md
│   │   ├── sections/
│   │   └── final_paper.md
│   └── resources/            # 相关资源
│       ├── references/
│       └── images/
```

### 3.2 元数据结构

```json
{
  "work_id": "12345",
  "title": "计算100平方的家庭使用空调降温速率研究",
  "description": "通过数学模型和数值模拟分析空调降温速率",
  "created_at": "2024-08-10T14:30:00Z",
  "updated_at": "2024-08-10T16:45:00Z",
  "status": "in_progress",
  "progress": {
    "modeling": 100,
    "coding": 80,
    "execution": 70,
    "writing": 60
  },
  "tags": ["热力学", "数值模拟", "空调"],
  "ai_models": {
    "central": "gpt-4",
    "code": "claude-3.5-sonnet",
    "paper": "gpt-4"
  }
}
```

## 4. 核心功能模块

### 4.1 工作管理模块

#### 4.1.1 工作创建
- 用户输入论文主题和描述
- 系统生成唯一工作ID
- 创建工作空间文件夹
- 初始化元数据和基础结构

#### 4.1.2 工作列表管理
- 显示所有历史工作
- 支持搜索和筛选
- 工作状态指示器
- 快速访问最近工作

#### 4.1.3 工作状态跟踪
- 实时进度更新
- 各阶段完成状态
- 时间消耗统计
- 资源使用情况

### 4.2 AI对话系统

#### 4.2.1 多系统协作
- **中枢系统**: 理解用户需求，协调其他系统
- **代码执行系统**: 生成、优化、执行代码
- **论文生成系统**: 撰写、格式化、优化论文

#### 4.2.2 对话流程
1. 用户输入需求
2. 中枢系统分析并规划
3. 调用相应子系统处理
4. 整合结果并回复用户
5. 保存对话历史

#### 4.2.3 上下文管理
- 维护工作上下文
- 记住用户偏好
- 支持多轮对话
- 历史对话检索

### 4.3 代码生成与执行

#### 4.3.1 代码生成
- 根据需求自动生成Python代码
- 支持多种编程范式
- 代码注释和文档
- 错误处理和边界情况

#### 4.3.2 代码执行
- 安全的沙箱环境
- 依赖管理
- 实时输出显示
- 错误捕获和调试

#### 4.3.3 结果处理
- 数据可视化
- 图表生成
- 结果导出
- 性能分析

### 4.4 论文生成系统

#### 4.4.1 论文结构
- 自动生成大纲
- 章节内容生成
- 图表和公式插入
- 参考文献管理

#### 4.4.2 格式支持
- Markdown格式
- LaTeX格式
- Word文档
- PDF导出

#### 4.4.3 质量控制
- 内容一致性检查
- 语法和拼写检查
- 学术规范验证
- 重复内容检测

## 5. 技术实现方案

### 5.1 前端技术栈

```typescript
// 核心组件
- WorkSpace.vue          # 工作空间主组件
- CodeEditor.vue         # 代码编辑器
- PaperEditor.vue        # 论文编辑器
- ExecutionPanel.vue     # 代码执行面板
- ProgressTracker.vue    # 进度跟踪器
- FileExplorer.vue       # 文件浏览器
```

### 5.2 后端API设计

```python
# 主要路由
POST   /api/works                    # 创建工作
GET    /api/works                    # 获取工作列表
GET    /api/works/{work_id}          # 获取工作详情
PUT    /api/works/{work_id}          # 更新工作信息
DELETE /api/works/{work_id}          # 删除工作

POST   /api/works/{work_id}/chat     # 发送对话消息
GET    /api/works/{work_id}/chat     # 获取对话历史

POST   /api/works/{work_id}/code     # 生成代码
POST   /api/works/{work_id}/execute  # 执行代码
GET    /api/works/{work_id}/results  # 获取执行结果

POST   /api/works/{work_id}/paper    # 生成论文
GET    /api/works/{work_id}/paper    # 获取论文内容
```

### 5.3 数据模型设计

```python
# 核心模型
class Work(BaseModel):
    id: str
    title: str
    description: str
    status: WorkStatus
    progress: Dict[str, int]
    created_at: datetime
    updated_at: datetime
    user_id: str

class ChatMessage(BaseModel):
    id: str
    work_id: str
    role: str
    content: str
    system_type: Optional[str]
    timestamp: datetime

class CodeFile(BaseModel):
    id: str
    work_id: str
    filename: str
    content: str
    language: str
    created_at: datetime

class ExecutionResult(BaseModel):
    id: str
    work_id: str
    code_file_id: str
    output: str
    error: Optional[str]
    execution_time: float
    created_at: datetime
```

## 6. 用户界面设计

### 6.1 工作空间布局

```
┌─────────────────────────────────────────────────────────────┐
│ 侧边栏 (工作列表) │ 主工作区                                │
│                 │ ┌─────────────────────────────────────┐ │
│ - 新建工作      │ │ 工作标题和状态                        │ │
│ - 历史工作      │ ├─────────────────────────────────────┤ │
│ - 用户信息      │ │ 标签页导航                            │ │
│                 │ │ [对话] [代码] [执行] [论文] [文件]   │ │
│                 │ ├─────────────────────────────────────┤ │
│                 │ │ 内容区域                              │ │
│                 │ │                                     │ │
│                 │ │                                     │ │
│                 │ └─────────────────────────────────────┘ │
└─────────────────┴─────────────────────────────────────────┘
```

### 6.2 标签页功能

#### 6.2.1 对话标签页
- 多系统对话界面
- 消息类型标识
- 操作按钮（复制、重新生成）
- 对话分割线

#### 6.2.2 代码标签页
- 代码编辑器
- 语法高亮
- 代码格式化
- 版本控制

#### 6.2.3 执行标签页
- 代码执行按钮
- 实时输出显示
- 错误信息展示
- 执行时间统计

#### 6.2.4 论文标签页
- 论文编辑器
- 实时预览
- 格式工具栏
- 导出选项

#### 6.2.5 文件标签页
- 文件树结构
- 文件预览
- 文件操作
- 搜索功能

## 7. 工作流程设计

### 7.1 典型工作流程

```
1. 创建工作
   ↓
2. 需求分析对话
   ↓
3. 数学模型建立
   ↓
4. 代码生成
   ↓
5. 代码执行和调试
   ↓
6. 结果分析和可视化
   ↓
7. 论文内容生成
   ↓
8. 论文格式化和导出
   ↓
9. 完成和归档
```

### 7.2 状态管理

```typescript
enum WorkStatus {
  CREATED = 'created',           // 已创建
  PLANNING = 'planning',         // 需求分析
  MODELING = 'modeling',         // 建模阶段
  CODING = 'coding',            // 代码开发
  EXECUTING = 'executing',      // 代码执行
  ANALYZING = 'analyzing',      // 结果分析
  WRITING = 'writing',          // 论文撰写
  REVIEWING = 'reviewing',      // 内容审查
  COMPLETED = 'completed',      // 已完成
  ARCHIVED = 'archived'         // 已归档
}
```

## 8. 安全与性能

### 8.1 安全措施
- 代码执行沙箱隔离
- 用户权限控制
- 文件访问限制
- API调用频率限制

### 8.2 性能优化
- 文件懒加载
- 对话历史分页
- 代码执行异步处理
- 缓存机制

### 8.3 可扩展性
- 模块化设计
- 插件系统支持
- 多AI模型支持
- 自定义工作流

## 9. 开发计划

### 9.1 第一阶段：基础架构
- 工作空间管理系统
- 基础文件操作
- 用户认证和权限

### 9.2 第二阶段：核心功能
- AI对话系统
- 代码生成和执行
- 基础论文生成

### 9.3 第三阶段：高级功能
- 多系统协作优化
- 高级论文格式
- 性能优化

### 9.4 第四阶段：完善和测试
- 用户界面优化
- 功能测试
- 性能测试
- 用户反馈收集

## 10. 技术挑战与解决方案

### 10.1 主要挑战
- AI模型协调复杂性
- 代码执行安全性
- 大文件处理性能
- 实时协作支持

### 10.2 解决方案
- 微服务架构
- 容器化部署
- 消息队列系统
- 分布式文件存储

## 11. 总结

这个AI论文生成功能将提供一个完整的端到端解决方案，让用户能够通过自然语言对话来生成、执行代码并撰写学术论文。系统采用模块化设计，支持多AI模型协作，具有良好的可扩展性和用户体验。

通过工作ID管理的工作空间系统，每个项目都有独立的环境和完整的文件管理，确保项目的隔离性和可维护性。同时，多系统协作的架构设计让AI能够更好地理解用户需求并提供专业的服务。
