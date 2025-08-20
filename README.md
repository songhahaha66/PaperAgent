# PaperAgent - 智能学术论文写作助手

<p align="center">
    <img src="./image/logo.png" height="250px">
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

### 为什么项目前端没有部署在Vercel等平台方便部署？

Vercel强制开启https，关于为什么不支持https下文提到了

### 为什么项目没有https部署？

前端若https部署，会强制要求后端api也https，项目后端服务器在国内，备案原因后端无法部署https。



### 手机打开部分页面不太适配？

此项目为论文自动写作工具，作为专业性强的项目。项目work界面一个界面就涉及到AI聊天界面，文件管理器，文件实时预览。三个组件同屏，对于小屏幕设备来说太困难。故部分页面没有对手机做优化显示。

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
FastAPI + Python 3.10+
├── 🚀 高性能异步API框架
├── 🗄️ PostgreSQL数据库 + SQLAlchemy ORM
├── 🔌 WebSocket实时通信
├── 🤖 LiteLLM多模型接入
├── 🛠️ 模块化工具系统
└── 🔒 JWT用户认证
```

### AI系统架构
```
多模型协同系统
├── 🧠 MainAgent (主控AI)
│   ├── 任务分析和规划
│   ├── 模型调度协调
│   └── 上下文管理
├── 💻 CodeAgent (代码AI)
│   ├── Python代码执行
│   ├── 数据分析处理
│   └── 可视化图表生成
└── 🛠️ Tools Framework
    ├── 文件操作工具
    ├── 模板处理工具
    └── 代码执行工具
```

## 🎯 独特优势

### 1. 🔄 多AI模型协同
- **专业化分工**：每个AI模型专注特定领域，提高专业性
- **智能调度**：主控AI根据任务特点选择最适合的模型
- **无缝协作**：模型间共享上下文，保证内容连贯性

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
Python 3.10+
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
uv venv
uv pip install -r pyproject.toml

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
uv run main.py
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
DATABASE_URL=postgresql://user:password@localhost:5432/paperagent

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
