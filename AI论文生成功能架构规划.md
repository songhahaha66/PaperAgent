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
│   ├── user_inputs/           # 用户初始输入和附件
│   │   ├── init.md            # 用户初始需求描述
│   │   ├── attachments/       # 用户上传的附件
│   │   └── requirements.txt   # 用户需求规格说明
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
- **主要格式**: Markdown (.md) - 简单易学，实时预览，版本控制友好
- **数学公式**: LaTeX数学语法 - 支持复杂数学公式和学术排版
- **导出格式**: PDF、LaTeX (.tex)、Word (.docx)、HTML
- **混合方案**: Markdown + LaTeX数学公式，兼顾易用性和专业性

#### 4.4.3 质量控制
- 内容一致性检查
- 语法和拼写检查
- 学术规范验证
- 重复内容检测

## 5. 技术实现方案

### 5.1 前端技术栈

```typescript
// 核心组件
- Work.vue               # 工作空间主组件
- Sidebar.vue            # 左侧边栏组件
- FileManager.vue        # 文件管理器组件
- ChatItem.vue           # 聊天消息组件
- ChatSender.vue         # 聊天输入组件

// 第三方组件
- TDesign Vue Next      # UI组件库
- Vue Router            # 路由管理
- Pinia                 # 状态管理
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

# 文件管理相关
POST   /api/files/upload             # 上传文件
GET    /api/files/{work_id}/list     # 获取工作空间文件列表
GET    /api/files/{work_id}/{path}   # 获取文件内容
PUT    /api/files/{work_id}/{path}   # 更新文件内容
DELETE /api/files/{work_id}/{path}   # 删除文件
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

基于现有的Vue3 + TDesign实现，工作空间采用左右分栏的布局设计：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 左侧边栏 (Sidebar)                    │ 主工作区 (Main Content)              │
│                                        │                                    │
│ ┌─────────────────────────────────────┐ │ ┌─────────────────────────────────┐ │
│ │ 工作管理                            │ │ │ 工作区头部 (Workspace Header)     │ │
│ │ - 新建工作                          │ │ │ - 工作标题                        │ │
│ │ - 历史工作列表                      │ │ │ - 创建时间                        │ │
│ │ - 用户信息                          │ │ └─────────────────────────────────┘ │
│ └─────────────────────────────────────┘ │                                    │
│                                        │ ┌─────────────────────────────────┐ │
│                                        │ │ 工作区内容 (Workspace Content)   │ │
│                                        │ │                                 │ │
│                                        │ │ ┌─────────────┐ ┌─────────────┐ │ │
│                                        │ │ │ 对话区域    │ │ 预览区域    │ │ │
│                                        │ │ │ (Chat)      │ │ (Preview)   │ │ │
│                                        │ │ │             │ │             │ │ │
│                                        │ │ │ - 消息列表  │ │ - 文件预览  │ │ │
│                                        │ │ │ - 文件管理  │ │ - 论文展示  │ │ │
│                                        │ │ │ - 输入框    │ │ - PDF预览   │ │ │
│                                        │ │ └─────────────┘ └─────────────┘ │ │
│                                        │ └─────────────────────────────────┘ │
└────────────────────────────────────────┴─────────────────────────────────────┘
```

#### 6.1.1 左侧边栏 (Sidebar)
- **工作管理**: 新建工作、历史工作列表
- **用户信息**: 用户头像、登录状态
- **折叠功能**: 支持侧边栏收缩展开
- **响应式设计**: 自适应不同屏幕尺寸

#### 6.1.2 主工作区 (Main Content)
- **工作区头部**: 显示当前工作标题和创建时间
- **工作区内容**: 左右分栏布局，左侧对话区域，右侧预览区域

#### 6.1.3 对话区域 (Chat Section)
- **消息列表**: 支持多系统对话，显示系统类型标签
- **文件管理器**: 可折叠的文件树结构，集成在对话区域
- **输入框**: 用户输入区域，支持发送消息

#### 6.1.4 预览区域 (Preview Section)
- **文件预览**: 支持多种文件格式的实时预览
- **论文展示**: 论文内容预览和PDF展示
- **动态内容**: 根据选中文件或工作状态动态显示内容

### 6.2 功能区域设计

#### 6.2.1 对话区域功能
- **多系统对话界面**: 支持中枢系统、代码执行系统、论文生成系统的协作对话
- **消息类型标识**: 通过系统标签区分不同AI系统的回复
- **操作按钮**: 复制消息内容、重新生成回复
- **对话分割线**: 交互式分割线，悬停时显示操作图标
- **消息变灰效果**: 悬停分割线时，后续消息会变灰，突出当前对话段落

#### 6.2.2 文件管理器功能
- **可折叠面板**: 使用TDesign的Collapse组件，支持展开/收缩
- **文件树结构**: 层级化的文件组织，支持文件夹展开
- **文件选择**: 点击文件节点触发文件预览
- **状态指示**: 显示当前选中的文件信息

#### 6.2.3 预览区域功能
- **动态内容显示**: 根据选中文件类型和内容动态渲染
- **多格式支持**: 
  - Python代码: 语法高亮显示
  - Markdown文档: 实时渲染预览
  - 文本文件: 纯文本显示
  - 日志文件: 结构化显示
- **论文展示**: 支持论文内容预览和PDF嵌入展示
- **响应式布局**: 自适应不同屏幕尺寸和内容长度

#### 6.2.4 工作状态管理
- **工作切换**: 通过侧边栏在不同工作间切换
- **状态保持**: 每个工作保持独立的对话历史和文件状态
- **实时更新**: 工作状态和进度实时更新

### 6.3 样式设计特点

#### 6.3.1 布局样式
- **全屏布局**: 使用`height: 100vh`和`width: 100vw`实现全屏显示
- **Flexbox布局**: 采用Flexbox实现响应式分栏布局
- **溢出处理**: 使用`overflow: hidden`防止页面滚动条
- **边框分隔**: 通过`border-right`和`border-top`实现区域分隔

#### 6.3.2 交互效果
- **悬停动画**: 对话分割线悬停时的展开动画效果
- **消息变灰**: 悬停分割线时后续消息的透明度变化
- **平滑过渡**: 使用`transition: all 0.3s ease`实现平滑动画
- **图标显示**: 悬停时显示操作图标的缩放动画

#### 6.3.3 响应式设计
- **最小宽度**: 对话区域设置`min-width: 300px`确保可用性
- **自适应高度**: 使用`flex: 1`实现区域自适应高度
- **内容滚动**: 消息列表和预览区域支持独立滚动
- **边距控制**: 通过padding和margin控制内容间距

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

## 8. 文件管理模式设计

### 8.1 文件管理器组件

#### 8.1.1 组件功能
- **文件树展示**: 使用TDesign的Tree组件展示层级文件结构
- **文件选择**: 支持点击选择文件，触发文件预览
- **状态管理**: 维护选中文件状态，支持文件操作事件
- **响应式设计**: 可折叠的文件管理器面板

#### 8.1.2 文件树结构
- **用户输入**: 初始需求描述、上传附件、需求规格说明等
- **生成的代码**: main.py、requirements.txt、数据文件等
- **执行结果**: output.log、图表、数据输出等
- **论文草稿**: 大纲、章节、最终论文等
- **相关资源**: 参考文献、图片等

### 8.2 文件预览系统

#### 8.2.1 多格式支持
- **Python代码**: 语法高亮显示，支持代码编辑
- **Markdown文档**: 实时渲染预览，支持LaTeX数学公式
- **LaTeX文档**: 专业学术排版，支持复杂数学公式
- **文本文件**: 纯文本显示，支持搜索
- **日志文件**: 结构化显示，支持过滤

#### 8.2.2 预览功能
- 根据文件类型动态渲染预览内容
- 支持实时编辑和内容更新
- 集成数学公式渲染引擎

### 8.3 后端文件服务

#### 8.3.1 文件操作API
- **文件上传**: 支持多种文件格式上传到工作空间
- **文件列表**: 获取工作空间文件列表
- **文件内容**: 读取、更新、删除文件内容
- **权限控制**: 基于用户身份的文件访问控制

#### 8.3.2 工作空间管理
- 自动创建工作空间目录结构
- 用户输入文件夹初始化（init.md、attachments、requirements.txt）
- 文件路径管理和冲突处理
- 文件元数据管理

### 8.4 文件同步与版本控制

#### 8.4.1 实时同步
- WebSocket连接支持多用户协作编辑
- 文件锁定机制防止并发冲突
- 实时变更通知和状态同步

#### 8.4.2 版本控制
- 集成Git版本控制系统
- 自动提交文件变更
- 文件修改历史追踪

### 8.5 文件安全与权限

#### 8.5.1 访问控制
- 基于JWT token的用户身份验证
- 工作空间隔离，确保数据安全
- 文件操作审计日志

#### 8.5.2 安全限制
- 文件类型白名单验证
- 文件大小和数量限制
- 恶意文件检测和过滤

### 8.6 论文格式选择与技术实现

#### 8.6.1 格式选择分析

##### LaTeX (.tex) 格式
**优势：**
- **学术标准**: 学术界广泛使用的标准格式
- **专业排版**: 数学公式、图表、参考文献等排版精美
- **期刊兼容**: 大多数学术期刊都支持LaTeX格式
- **版本控制友好**: 纯文本格式，适合Git版本控制

**实现难度：**
- **中等**: 需要LaTeX编译环境
- **语法学习**: 有一定的学习曲线
- **编译依赖**: 需要安装TeX发行版（如TeX Live、MiKTeX）

##### Markdown (.md) 格式
**优势：**
- **简单易学**: 语法简单，学习成本低
- **广泛支持**: 大多数平台和工具都支持
- **实时预览**: 可以实时渲染预览
- **轻量级**: 文件小，处理快速

**局限性：**
- **功能有限**: 复杂数学公式、表格等支持有限
- **样式限制**: 排版样式相对简单
- **学术认可度**: 在正式学术发表中认可度较低

#### 8.6.2 推荐方案：混合使用

基于实现难度和功能需求，采用**Markdown + LaTeX数学公式**的混合方案：

```typescript
// 文件格式支持策略
const PAPER_FORMATS = {
  primary: 'markdown',           // 主要使用Markdown
  math_support: 'latex_math',    // 数学公式使用LaTeX语法
  export_formats: ['md', 'tex', 'pdf', 'docx']  // 支持多种导出格式
}

// 文件内容示例
const paperContent = `# 论文标题

## 摘要
这是论文摘要内容...

## 1. 引言
这是引言部分...

## 2. 数学模型
使用LaTeX数学公式：

$$\\frac{dT}{dt} = \\frac{P}{mc}(T_{out} - T)$$

其中：
- $T$ 是温度
- $P$ 是制冷功率
- $m$ 是空气质量
- $c$ 是比热容

## 3. 代码实现
\`\`\`python
import numpy as np
import matplotlib.pyplot as plt
# ... 代码内容
\`\`\`

## 4. 结果分析
分析结果...

## 参考文献
1. 作者名. 论文标题. 期刊名, 年份
`
```

#### 8.6.3 技术实现方案

##### 前端编辑器
```typescript
// 使用支持LaTeX数学公式的Markdown编辑器
import { Editor } from '@toast-ui/editor'
import 'katex/dist/katex.min.css'

// 配置数学公式支持
const editor = new Editor({
  el: document.querySelector('#editor'),
  height: '600px',
  initialEditType: 'markdown',
  previewStyle: 'vertical',
  math: {
    engine: 'KaTeX',
    inlineMath: [['$', '$'], ['\\(', '\\)']],
    blockMath: [['$$', '$$'], ['\\[', '\\]']]
  }
})
```

##### 后端处理服务
```python
class PaperFormatService:
    """论文格式处理服务"""
    
    def __init__(self):
        self.md_processor = MarkdownProcessor()
        self.latex_converter = LaTeXConverter()
    
    def markdown_to_latex(self, md_content: str) -> str:
        """将Markdown转换为LaTeX格式"""
        # 处理Markdown语法
        processed_content = self.md_processor.process(md_content)
        
        # 转换为LaTeX
        latex_content = self.latex_converter.convert(processed_content)
        return latex_content
    
    def markdown_to_pdf(self, md_content: str) -> bytes:
        """将Markdown转换为PDF"""
        # 先转换为LaTeX
        latex_content = self.markdown_to_latex(md_content)
        
        # 编译LaTeX生成PDF
        pdf_bytes = self.compile_latex(latex_content)
        return pdf_bytes
    
    def compile_latex(self, latex_content: str) -> bytes:
        """编译LaTeX代码生成PDF"""
        # 使用pandoc或直接调用LaTeX编译器
        import subprocess
        import tempfile
        import os
        
        # 创建临时.tex文件
        with tempfile.NamedTemporaryFile(suffix='.tex', mode='w', delete=False) as f:
            f.write(latex_content)
            tex_file = f.name
        
        try:
            # 使用pdflatex编译
            result = subprocess.run([
                'pdflatex', 
                '-interaction=nonstopmode', 
                tex_file
            ], capture_output=True, text=True)
            
            # 读取生成的PDF
            pdf_file = tex_file.replace('.tex', '.pdf')
            with open(pdf_file, 'rb') as f:
                return f.read()
                
        finally:
            # 清理临时文件
            os.unlink(tex_file)
            if os.path.exists(pdf_file):
                os.unlink(pdf_file)
```

#### 8.6.4 文件结构设计

```
paper_drafts/
├── outline.md              # 论文大纲 (Markdown)
├── sections/               # 章节内容
│   ├── introduction.md     # 引言
│   ├── methodology.md      # 方法
│   ├── results.md          # 结果
│   └── conclusion.md       # 结论
├── final_paper.md          # 完整论文 (Markdown)
├── final_paper.tex         # LaTeX版本
├── final_paper.pdf         # PDF版本
└── references/             # 参考文献
    ├── bibliography.bib    # BibTeX格式
    └── citations.md        # Markdown格式
```

#### 8.6.5 实现优先级

1. **第一阶段**: Markdown基础编辑器，支持基本语法
2. **第二阶段**: LaTeX数学公式支持，使用KaTeX渲染
3. **第三阶段**: 格式转换功能，支持导出为.tex、.pdf
4. **第四阶段**: 高级排版功能，参考文献管理

## 9. 安全与性能

### 9.1 安全措施
- 代码执行沙箱隔离
- 用户权限控制
- 文件访问限制
- API调用频率限制

### 9.2 性能优化
- 文件懒加载
- 对话历史分页
- 代码执行异步处理
- 缓存机制

### 9.3 可扩展性
- 模块化设计
- 插件系统支持
- 多AI模型支持
- 自定义工作流

## 10. 开发计划

### 10.1 第一阶段：基础架构
- 工作空间管理系统
- 基础文件操作
- 用户认证和权限

### 10.2 第二阶段：核心功能
- AI对话系统
- 代码生成和执行
- 基础论文生成

### 10.3 第三阶段：高级功能
- 多系统协作优化
- 高级论文格式
- 性能优化

### 10.4 第四阶段：完善和测试
- 用户界面优化
- 功能测试
- 性能测试
- 用户反馈收集

## 11. 技术挑战与解决方案

### 11.1 主要挑战
- AI模型协调复杂性
- 代码执行安全性
- 大文件处理性能
- 实时协作支持

### 11.2 解决方案
- 微服务架构
- 容器化部署
- 消息队列系统
- 分布式文件存储

## 12. 总结

这个AI论文生成功能将提供一个完整的端到端解决方案，让用户能够通过自然语言对话来生成、执行代码并撰写学术论文。系统采用模块化设计，支持多AI模型协作，具有良好的可扩展性和用户体验。

通过工作ID管理的工作空间系统，每个项目都有独立的环境和完整的文件管理，确保项目的隔离性和可维护性。同时，多系统协作的架构设计让AI能够更好地理解用户需求并提供专业的服务。

### 12.1 文件管理模式亮点

1. **统一文件树结构**: 采用标准化的文件组织方式，便于用户理解和管理
2. **用户输入管理**: 专门存储用户初始需求和上传附件，便于追溯项目起源
3. **实时文件预览**: 支持多种文件格式的即时预览和编辑
4. **权限隔离**: 基于用户身份的文件访问控制，确保数据安全
5. **版本控制**: 集成Git版本控制，支持文件变更历史追踪
6. **响应式设计**: 文件管理器可折叠，适应不同屏幕尺寸
7. **多格式支持**: 支持代码、文档、数据、图片等多种文件类型
8. **实时同步**: 支持多用户协作编辑，实时同步文件变更

### 12.2 用户界面设计亮点

1. **左右分栏布局**: 采用直观的左右分栏设计，左侧对话，右侧预览
2. **集成式文件管理**: 文件管理器集成在对话区域，不占用额外空间
3. **多系统对话**: 支持中枢、代码、论文三个AI系统的协作对话
4. **交互式分割线**: 悬停时显示操作图标，提升用户体验
5. **动态内容预览**: 根据选中文件类型动态渲染预览内容
6. **全屏工作环境**: 100vh全屏设计，最大化工作空间利用率
7. **TDesign集成**: 使用TDesign组件库，保持界面风格统一

### 12.3 论文格式设计亮点

1. **混合格式方案**: Markdown + LaTeX数学公式，兼顾易用性和专业性
2. **多格式支持**: 支持.md、.tex、.pdf、.docx等多种导出格式
3. **数学公式渲染**: 集成KaTeX引擎，支持复杂数学公式显示
4. **实时预览**: Markdown编辑器支持实时渲染和数学公式预览
5. **格式转换**: 自动转换Markdown为LaTeX和PDF格式
6. **版本控制友好**: 纯文本格式，适合Git版本控制
7. **学术标准兼容**: 支持学术期刊的LaTeX格式要求
