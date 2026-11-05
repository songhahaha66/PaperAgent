# PaperAgent

[![wakatime](https://wakatime.com/badge/user/ef685785-b2de-4416-b5c6-df540c453238/project/e2409803-47c5-4f66-835f-714caf1d3d9e.svg)](https://wakatime.com/badge/user/ef685785-b2de-4416-b5c6-df540c453238/project/e2409803-47c5-4f66-835f-714caf1d3d9e)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/songhahaha66/PaperAgent)

<p align="center">
  <img src="./image/PaperAgent.png" height="96" alt="PaperAgent">
</p>

提出问题，让 AI 自主研究。上传题目与数据，PaperAgent 自主建模、运行实验、分析结果，并将研究过程与结论写成论文，适用于数学建模、数据分析和研究写作。

[在线体验](https://paperagent.tech/) · [示例论文](example/paper.pdf)

## 功能

- **自主研究** — 读取材料、制定计划、调用工具，根据实验结果继续分析和调整方法。
- **代码实验** — 在沙箱中运行 Python，处理数据、求解模型、生成图表，并保存代码与计算结果。
- **论文写作** — 将研究结果写入正文，支持 LaTeX / PDF、Word、Markdown，以及自定义 Word 和 Markdown 模板。
- **交互修改** — 在对话中追问、补充要求，也可以选中论文段落提出问题或修改意见。
- **研究工作区** — 在同一界面查看对话、论文、文件与运行输出，支持分屏预览和中断后继续。

**数模真题示例**采用 2026 年全国大学生数学建模竞赛 A 题《药材的烘干问题》，包含传热传质建模、数值求解、模型检验和灵敏度分析。示例包提供题目附件、代码、图表、四份结果表及 17 页论文。 [查看论文](example/paper.pdf) · [下载示例包](example/herb-drying.zip)

## 架构

| 层次 | 技术与职责 |
| --- | --- |
| 前端 | Vue 3、TypeScript、TDesign、Pinia、Vite，提供对话、论文预览与文件工作区 |
| 后端 | FastAPI、SQLAlchemy、WebSocket，处理认证、项目、文件和实时任务消息 |
| Agent | LangGraph 编排研究流程，LangChain 接入模型，按需调用计算、文献和写作工具 |
| 执行环境 | OpenSandbox 运行 Python 实验，以及 Pandoc、XeLaTeX、BibTeX 等文档工具 |
| 数据存储 | PostgreSQL 保存账户与项目；SQLite 保存研究状态、事件和执行位置；`pa_data` 保存资料、成果与运行记录 |

```text
用户输入问题与附件
        ↓
FastAPI 接收消息 → LangGraph 识别意图
        ↓
研究 Agent
  读取材料 → 制定计划 → 调用工具 → 观察结果 → 决定下一步
                         │                       │
                         ├─ Python 建模与计算    └─ 继续实验或编辑论文
                         ├─ 文献检索与引用
                         └─ 论文编辑与导出
        ↓
代码、图表、结果表与论文 → 工作区实时展示
```

研究计算通过独立 Python 进程执行，代码模块和数据文件供后续步骤复用。运行回执、输出文件和执行位置持久保存，恢复任务时先核对已有执行记录，再继续处理。资料与正式成果保存在后端，按需通过文件 API 传入沙箱执行。

流程细节见 [研究 Agent 说明](backend/ai_system/research/README.md)，沙箱连接与部署见 [执行环境说明](deploy/sandbox/README.md)。

## 快速启动

### 环境准备

- Python 3.14+ 和 uv。
- Node.js 20.19+（20.x）或 22.12+，以及 npm。
- PostgreSQL，准备好数据库与有建表权限的账户。
- 可访问的 OpenSandbox 1.1.1 服务与 `paperagent-research:workspace-v2` 执行镜像，用于研究计算和文档导出。搭建步骤见 [沙箱部署说明](deploy/sandbox/README.md)。

以下命令在 macOS / Linux 终端执行。

### 1. 获取代码

```bash
git clone https://github.com/songhahaha66/PaperAgent.git
cd PaperAgent
```

### 2. 配置并启动后端

```bash
cd backend
uv sync --frozen --extra sandbox
cp .env.example .env
cp ../deploy/sandbox/client.env.example .sandbox.env
```

编辑 `backend/.env`，填写数据库连接、随机生成的 JWT 密钥和沙箱配置路径。

```dotenv
DATABASE_URL=postgresql+psycopg://username:password@localhost:5432/paperagent
SECRET_KEY=replace-with-a-random-secret
PAPERAGENT_SANDBOX_CONFIG=.sandbox.env
```

编辑 `backend/.sandbox.env`，将服务地址、API Key、镜像名和服务器缓存目录填写为实际部署值。配置字段见 [沙箱配置示例](deploy/sandbox/client.env.example)。

在 `backend` 目录启动服务，应用会自动执行数据库迁移。

```bash
uv run --frozen --extra sandbox uvicorn main:app --host 127.0.0.1 --port 8000
```

后端启动后可访问 [健康检查](http://localhost:8000/health) 和 [API 文档](http://localhost:8000/docs)。

### 3. 启动前端

打开另一个终端，从项目根目录执行。

```bash
cd frontend
npm ci
cp .env.example .env
npm run dev
```

前端默认连接 `http://localhost:8000`，可通过 `frontend/.env` 中的 `VITE_API_BASE_URL` 调整。

### 4. 开始研究

打开 [本地页面](http://localhost:5173/)，注册并登录，在「模型设置」中配置模型服务地址、API Key 和模型名称。返回工作台，输入研究问题、上传附件、选择输出格式，即可开始研究。

生成的资料和成果默认保存在项目根目录的 `pa_data` 中，可通过后端环境变量 `PA_DATA_PATH` 指定存储位置。
