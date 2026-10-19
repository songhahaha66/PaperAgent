# PaperAgent - 智能学术论文写作助手

[![wakatime](https://wakatime.com/badge/user/ef685785-b2de-4416-b5c6-df540c453238/project/e2409803-47c5-4f66-835f-714caf1d3d9e.svg)](https://wakatime.com/badge/user/ef685785-b2de-4416-b5c6-df540c453238/project/e2409803-47c5-4f66-835f-714caf1d3d9e)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/songhahaha66/PaperAgent)
<p align="center">
    <img src="./image/PaperAgent.png" height="96px">
</p>

PaperAgent 是一个基于人工智能技术的学术论文写作助手，旨在显著提升研究人员、学生和学术写作者的论文撰写效率和质量。通过集成先进的AI模型和智能工具，PaperAgent 能够协助用户完成从研究、数据分析到论文写作的全流程工作。

## 部分问题QA（建议阅读）

### 一句话概括你们的项目

AI写作论文，我们项目支持 输入问题->AI自动写作论文（支持论文嵌入图片、数据等，由AI生成代码自动执行得到）

### 你们的项目有什么应用场景

数模比赛、部分论文辅助写作


### 有没有推荐问题方便我测试你们的项目？

```
用蒙特卡洛方法估计π的值，并绘制收敛过程图
```

```
研究数学三角函数图像的特性，比如正弦波、余弦波的叠加
```

### 有没有例子？

给你们的测试账号有历史记录，另外请查看example文件夹，这是跑出来的结果，人工0修改！




### 手机上怎么用工作区？

每个工作内通过标签查看对话、文件、图表和报告。拖动标签到边缘可形成双窗格，窄屏下窗格上下排列；侧栏在手机上以抽屉打开。研究结果卡片可以回查对应执行代码和输出。

## 核心功能

### 1. 智能论文生成
- 基于AI的论文内容自动生成
- 支持多种学术论文模板
- 实时渲染和预览论文内容

### 2. 代码实验与数据分析
- 在线编写和运行Python代码
- 数据读取、分析和可视化图表生成
- 支持调用搜索引擎获取最新信息

### 3. 灵活的模板系统
- 支持自定义论文模板
- 模板上传、管理和复用功能
- 模板与AI生成内容无缝集成

### 4. 实时交互与反馈
- 类似ChatGPT的对话式交互体验
- 支持用户中途修改需求并快速调整方向

### 5. 完整文件管理
- 可视化文件浏览器
- 支持多种文件格式（.py, .md, .png等）


## 🏗️ 技术架构

### 前端技术栈
```
Vue 3 + TypeScript + TDesign
├── 🎨 现代化UI组件库 (TDesign Vue Next)
├── 🔄 状态管理 (Pinia + 持久化)
└── ⚡ 高性能构建 (Vite)
```

### 后端技术栈
```
FastAPI + Python 3.14+
├── 🚀 高性能异步API框架
├── 🗄️ PostgreSQL数据库 + SQLAlchemy ORM
├── 🔌 WebSocket实时通信
├── 🤖 LangChain 多模型接入
├── 🛠️ 模块化工具系统
└── 🔒 JWT用户认证
```

### AI系统架构

```text
PaperGraphAgent（LangGraph）
├── 原题要求（逐字来源、任务范围、验收检查）+ 版本化假设（范围、可证伪条件）
├── 依赖调度 → 条件触发候选试跑 → 独立尝试工作区 / Python 内核求解
├── 结果登记 + 要求证据映射 → 固定领域检查 → 结论一致性审查
│   └── 疑点 → 独立核验 → 反证确认 → 修改计划/假设 → 上游及受影响下游重算
├── 引用驱动写作 → 章节检查与定向修复
│   ├── LaTeX：原生章节源码 → paper.tex → XeLaTeX → paper.pdf
│   └── Markdown / Word：Markdown 章节 → paper.md / paper.docx
└── ResearchStore（SQLite 业务状态与事件日志）+ LangGraph Checkpoint（执行位置）

FastAPI 任务与对话
├── 显式动作：追问、执行、确认、修改方案、恢复、暂停
├── 只读追问：已有结果与报告上下文，不调用求解图
└── WebSocket：task_id / seq、增量回放、研究快照
```

当前研究协议为 `research-v3`（快照 schema 2）。要求、假设、验收结果和问题生命周期在同一业务状态中记录；提交结果时重跑验收，并核对冻结输入、任务/假设版本和检查指纹，过期结果不能覆盖当前结果。局部审查使用同一问题协议，返工后必须复审当前报告；审查故障和未关闭问题会阻止交付。设计、求解中新采用的关键假设先登记版本再冻结重算，未选中候选的假设不进入正式状态。计划修改不能删除原题要求或放宽其检查，只重算受影响任务，保留无关结果；规划和执行都有预算上限。旧协议研究仍可只读查看，需要重新发起研究才能执行，不做协议迁移。

领域验收支持结果存在性、有限数、阈值比较、跨任务结果比较和误差容限。硬约束必须有可执行检查，目标/交付物必须有任务覆盖，计算任务还需登记证据映射。模型提出的研究疑点先独立复算固定检查，确认反证后才能撤销结果；假设被反证后必须修订或显式停用。缺少检查、缺少唯一指标、核验失败和审查服务故障会阻止交付，不会视作通过。无法操作化的假设保留为假设，不宣称已验证；这些检查也不等同于对模型科学有效性的普遍证明。

只读追问通过 `AI_RESEARCH_CONTEXT_ENABLED=1` 启用，当前允许的模型目标为 HTTPS DeepSeek 官方 API 或 `aigw.saurlax.com` 网关（精确域名、默认 HTTPS 端口）。发送范围是当前工作的登记结果、报告与输出节选、校验摘要和近期对话；未启用时仍可在本地查看研究结果与来源。

## 🎯 独特优势

### 1. 🔄 多AI模型协同
- **专业化分工**：每个AI模型专注特定领域，提高专业性
- **按角色配置**：分析、编码和写作分别使用配置的模型
- **证据与结论协作**：通过登记结果和一致性审查发现内容冲突

### 2. 📝 全流程写作支持
- **从构思到成文**：覆盖论文写作的每个环节
- **研究-实验-写作**：三位一体的完整workflow
- **实时反馈调整**：写作过程中随时优化调整

### 3. 🎨 模板驱动生成
- **格式标准化**：确保符合学术规范
- **内容结构化**：基于模板智能组织内容
- **一键适配**：轻松切换不同格式

### 4. 💡 智能交互体验
- **自然语言操作**：像聊天一样完成复杂任务
- **实时可视化**：即时预览生成效果
- **渐进式完善**：逐步优化，达到最佳效果

## 📦 运行部署指南

### 环境要求
```bash
# 基础环境
Python 3.14+
Node.js 20.19.0+ || 22.12.0+
PostgreSQL 12+
Git
```

### 快速启动

#### 1️⃣ 克隆项目
```bash
git clone https://github.com/songhahaha66/PaperAgent.git
cd PaperAgent
```

#### 2️⃣ 后端部署

```bash
# 进入后端目录
cd backend

# 安装uv包管理工具
pip install uv

# 创建虚拟环境并安装依赖
uv sync --frozen

# 配置环境变量
cp .env.example .env
# 编辑.env文件，配置数据库连接和其他参数

# 激活虚拟环境
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Windows CMD
.venv\Scripts\activate.bat
# Linux/macOS
source .venv/bin/activate

# 启动后端服务
uv run --frozen uvicorn main:app --host 127.0.0.1 --port 8000
```

#### 3️⃣ 前端部署

```bash
# 进入前端目录
cd frontend

# 安装依赖
npm install

# 开发模式启动
npm run dev

# 生产构建
npm run build
```

### 🔧 配置说明

#### 后端环境变量 (.env)
```env
# 数据库配置
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/paperagent

# JWT配置
SECRET_KEY=your-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=30

# AI模型配置（在管理界面配置）
# 支持OpenAI、Anthropic、Google等多种服务商
```

#### 前端配置(.env.example)
```javascript
VITE_API_BASE_URL=
VITE_APP_TITLE=
VITE_APP_VERSION=
```

## 🎯 适用场景

### 👨‍🎓 学术研究人员
- 快速生成研究论文草稿
- 数据分析和可视化
- 文献综述和相关工作整理

### 🎓 在校学生
- 课程作业和实验报告
- 毕业论文和学位论文
- 学术演示和报告

### 💼 专业写作者
- 技术文档和白皮书
- 研究报告和分析文档
- 行业报告和调研材料

### 📊 数据分析师
- 数据分析报告
- 可视化图表生成
- 实验结果文档化

---

**🌟 PaperAgent：让AI成为你的学术写作伙伴，开启智能化论文创作新时代！**

文献检索、原文摘录与引用核查的使用方法及验证范围见 [文献功能说明](LITERATURE.md)。
