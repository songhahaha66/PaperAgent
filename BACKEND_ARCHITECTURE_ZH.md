# 后端架构文档

本文档详细概述了 PaperAgent 应用的后端架构。

## 1. 高层概览

后端是一个使用 Python 和 FastAPI 构建的现代化异步 Web 服务。它被设计为一个健壮、可扩展且模块化的系统，为 PaperAgent 的 AI 驱动功能提供支持。

### 技术栈

- **框架**: **FastAPI** 用于实现高性能的异步 API 端点。
- **数据库**: **PostgreSQL** 用于关系数据存储，通过 **SQLAlchemy** ORM 进行访问。
- **AI 集成**: **LiteLLM** 提供一个统一接口，用于访问各种大型语言模型 (LLM)。
- **身份认证**: **JWT (JSON Web Tokens)** 用于保护 API 端点的安全。
- **实时通信**: **WebSockets** 用于流式传输 AI 响应并实现交互式用户会话。
- **代码执行环境**: 一个沙盒环境，包含常见的数据科学库（`pandas`、`numpy`、`matplotlib`），用于执行 AI 生成的代码。

### 核心架构原则

- **异步**: 从头开始使用 `async`/`await` 构建，以高效处理并发操作，这对于管理长时间运行的 AI 任务和 WebSocket 连接至关重要。
- **模块化**: 代码库被组织成清晰、解耦的模块，各自承担明确的职责（例如 `routers`, `services`, `models`, `ai_system`）。
- **有状态会话**: 系统管理以 `Work` 为中心的有状态用户会话，每个会话由唯一的 `work_id` 标识。这使得 AI 可以在较长时间内为特定任务维护上下文和管理文件。
- **基于 Agent 的 AI**: AI 逻辑围绕一个基于 Agent 的模型构建。一个中央协调器 Agent (`MainAgent`) 将任务委托给专门的 Agent 和工具，从而将高层规划与底层执行分离开来。

## 2. 系统组件

该架构可分为几个关键组件：

### 2.1. API 层 (Routers)

API 层是所有客户端请求的入口点。它使用 FastAPI 的 `APIRouter` 来保持端点的组织性。

- **WebSocket 端点 (`/api/chat/ws/{work_id}`):** 这是交互式 AI 聊天的主要通信渠道。它处理身份认证、会话管理以及 AI 生成内容（包括文本、工具调用和最终结果）的实时流式传输。
- **REST 端点:** 为辅助功能提供了标准的 RESTful 端点，例如：
    - 用户认证 (`/api/auth`)。
    - 管理论文模板 (`/api/templates`)。
    - `Work` 实体的 CRUD 操作 (`/api/works`)。
    - 检索聊天记录 (`/api/chat/work/{work_id}/history`)。

### 2.2. 服务层 (Service Layer)

服务层包含应用的核心业务逻辑，作为 API 层和数据层之间的桥梁。

- **`ChatService`**: 管理聊天对话的持久化。它从文件系统上的 JSON 文件中读取和写入数据，每个文件对应一个特定的 `Work`。
- **`CRUD Service`**: 为数据库模型提供通用的创建、读取、更新和删除操作。

### 2.3. AI Agent 系统 (`ai_system`)

这是后端最复杂和最关键的部分，负责所有与 AI 相关的逻辑。

- **`MainAgent` (协调器):** 这是系统的“大脑”。它接收用户的提示，维护对话历史，并决定行动方案。它不直接执行任务，而是通过调用工具来委托任务。
- **`CodeAgent` (专家):** 一个由 `MainAgent` 调用的专门 Agent，用于处理需要代码执行的任务，如数据分析、数学计算或生成图表。它在一个安全、隔离的环境中运行。
- **`LLMHandler`**: `LiteLLM` 的一个包装器，管理与外部语言模型的实际通信。它处理向调用者流式传输响应。
- **工具系统 (Tool System)**: `MainAgent` 可以调用的一组函数，用于与其环境交互。这包括以下工具：
    - 文件 I/O (`writemd`, `tree`)。
    - 模板操作 (`update_section_content`, `analyze_template`)。
    - 代码执行 (通过调用 `CodeAgent`)。
- **`ContextManager`**: 管理对话历史，以确保其保持在 LLM 的上下文窗口限制内。它使用摘要和压缩技术来精简对话的旧部分。

### 2.4. 数据持久化层

应用程序采用混合方法进行数据存储，同时利用关系数据库和文件系统。

- **PostgreSQL 数据库**:
    - **模式**: 在 `models/models.py` 中使用 SQLAlchemy 定义。
    - **存储内容**: 核心关系数据，如 `User` 个人资料、`Work` 元数据、`PaperTemplate` 信息和 `ModelConfig` 设置。
- **文件系统 (`pa_data/`)**:
    - **工作区 (`pa_data/workspaces/{work_id}/`)**: 每个 `Work` 在文件系统上都有一个专用目录。该目录充当 AI 的沙盒工作区，存储生成的文件、源代码、图像和最终论文 (`paper.md`)。
    - **聊天记录 (`pa_data/chat_history/{work_id}.json`)**: 每个 `Work` 的聊天日志都存储为结构化的 JSON 文件。选择这种方法而不是数据库，是为了更好地处理 AI 消息的复杂嵌套结构（可能包括文本、工具调用和 JSON 块），并提高长对话的读/写性能。
    - **模板 (`pa_data/templates/`)**: 存储论文模板的 Markdown 文件。

## 3. 请求生命周期 (WebSocket 聊天)

为了说明各组件如何协同工作，以下是通过聊天界面发送的典型用户消息的生命周期：

1.  **连接**: 前端建立到 `/api/chat/ws/{work_id}` 的 WebSocket 连接。
2.  **身份认证**: 客户端发送一个 JWT 令牌，服务器对其进行验证。服务器还会验证经过身份认证的用户是否有权访问指定的 `work_id`。
3.  **消息接收**: 用户通过 WebSocket 发送一条消息（提示）。
4.  **Agent 初始化**: 服务器实例化 `MainAgent`，并为其提供 `work_id` 和关联的 `template_id`。同时，它还初始化 `ChatService`、`LLMHandler` 和 `PersistentStreamManager`。
5.  **历史记录加载**: `ChatService` 从相应的 JSON 文件中加载现有的聊天历史记录，并将此历史记录传递给 `MainAgent` 以提供上下文。
6.  **Agent 执行**: `MainAgent` 接收新的用户提示。它将提示附加到其对话历史中，并进入其执行循环：
    a. 它通过 `LLMHandler` 将当前的对话历史发送给 LLM。
    b. LLM 响应一个最终答案或一个调用一个或多个工具的请求。
    c. 如果 LLM 请求进行工具调用（例如，运行一些 Python 代码），`MainAgent` 会调用适当的工具（例如 `CodeAgent`）。然后，该工具的输出被添加到对话历史中。
    d. 重复此过程（步骤 a-c），直到 LLM 提供最终答案并且任务完成。
7.  **实时流式传输**: 在 Agent 的整个执行过程中，`PersistentStreamManager` 通过 WebSocket 将所有事件（LLM 内容、工具调用、状态更新）实时流式传输回前端。
8.  **持久化**: `PersistentStreamManager` 与 `ChatService` 协同工作，将整个交互（用户提示、AI 响应、工具调用）保存到聊天历史 JSON 文件中，确保对话得以保留。
9.  **完成**: 向客户端发送一条最终的“完成”消息，服务器等待下一条消息。

## 4. 架构图

```mermaid
graph TD
    subgraph 客户端
        A[浏览器 - Vue.js 前端]
    end

    subgraph 后端
        B[FastAPI 应用]
        subgraph 路由 (Routers)
            C[WebSocket 端点]
            D[REST 端点]
        end
        subgraph 服务 (Services)
            E[ChatService]
            F[CRUD Service]
        end
        subgraph "AI Agent 系统"
            G[MainAgent - 协调器]
            H[CodeAgent - 专家]
            I[LLMHandler]
            J[工具系统]
            K[ContextManager]
        end
        subgraph "数据持久化"
            L[PostgreSQL 数据库]
            M[文件系统 - pa_data/]
        end
    end

    subgraph 外部服务
        N[大型语言模型 - LLM]
    end

    A -- HTTP/WebSocket --> B
    B --> C & D
    C -- 管理 --> G
    D -- 使用 --> F

    F -- 交互 --> L

    G -- 委托 --> H
    G -- 使用 --> J
    G -- 使用 --> K
    G -- 使用 --> I
    I -- 通信 --> N

    H -- 在...中执行代码 --> M[工作区]

    C -- 使用 --> E
    E -- 读/写 --> M[聊天记录]

    J -- 交互 --> M[工作区]
```
