# PaperAgent

PaperAgent 是一个 AI 驱动的论文自动生成系统，能够协助用户高效地完成学术论文写作。

## 功能特性

### 后端核心功能

- **中枢大脑**：整体协调和决策
- **代码实验**：编写并运行 Python 代码，读取数据、分析数据、生成图表等
- **论文写作**：负责论文内容生成与格式化呈现

### 交互体验

- **实时反馈**：用户可实时了解当前任务进度，包括：
  - 实时渲染论文内容
  - (dev) debug 控制台显示日志
- **动态调整**：支持用户中途提出改进意见，AI 快速调整方向
- **对话式交互**：类似 ChatGPT 的交互体验，支持上下文理解
- **版本控制**：每次对话都通过 git 提交，并可随时回滚

### 论文模板系统

- **自定义模板支持**：用户可上传 Word 等文档，转换为 LaTeX 模板供 AI 生成论文格式

## 技术架构

### 前端架构

- [Vue 3](https://vuejs.org/) + [TypeScript](https://www.typescriptlang.org/)
- [TDesign Vue Next](https://tdesign.tencent.com/vue-next) 组件库
- [TD Chat](https://tdesign.tencent.com/vue-next/components/chat) AI 对话组件
- [Pinia](https://pinia.vuejs.org/) 状态管理
- [Vue Router](https://router.vuejs.org/) 路由管理
- [Vite](https://vitejs.dev/) 构建工具

### 后端架构

- [FastAPI](https://fastapi.tiangolo.com/) Python 框架
- [uv](https://github.com/astral-sh/uv) Python 包管理器
- [PostgreSQL](https://www.postgresql.org/) 数据库
- [SQLAlchemy](https://www.sqlalchemy.org/) ORM
- [JWT](https://jwt.io/) 认证

### 系统集成

- **模型管理服务**：统一管理多个 AI 模型
- **任务调度系统**：协调不同模型的工作流程
- **实时通信**：WebSocket 支持实时状态更新
- **文件管理**：LaTeX 文件、代码文件、数据文件管理