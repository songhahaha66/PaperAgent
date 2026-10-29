# LangSmith 追踪

PaperAgent 通过 LangGraph / LangChain 的原生回调接入 LangSmith。研究执行、断点恢复、定向修订和论文评论共用研究标识；模块注册入口记录具体能力的输入、输出和异常。原来的 `.system/llm_calls.json`、研究业务库和执行文件继续保留。

## 配置与验证

在 `backend/.env` 配置（不要把 key 提交到 Git）：

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=你的key
LANGSMITH_PROJECT=PaperAgent
# 美国区默认值；其他区域或自托管应使用账号对应的 endpoint
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
# key 可访问多个工作区时填写
# LANGSMITH_WORKSPACE_ID=工作区ID
```

依赖用 `uv sync` 安装。重启后端后，新执行开始上传。未启用或缺少 key 时不上传，后端启动日志显示状态。修改 `.env` 后需要重启后端；不要为追踪中断正在运行的研究。

先运行无模型费用的连通性检查（在 `backend` 目录）：

```sh
uv run python -m evals.langsmith_smoke
```

检查用假模型执行一个小 LangGraph 和一个测试能力，上传后从 LangSmith 读回完整的模型、能力子步骤，再打印可打开的 trace 链接。无 key、认证失败、上传失败或未读回完整 trace 都不会报告成功。

## 查看真实执行

打开 [LangSmith](https://smith.langchain.com/)，进入配置的工作区、Tracing 和 `PaperAgent` 项目。按 Metadata 的 `work_id` 筛选工作，如 `c1eb8d7c`；展开 `PaperAgent.research` / `PaperAgent.resume` 看图节点、模型消息和具体能力（如 `literature.collect`）。修订规划显示为 `PaperAgent.revise`，论文评论显示为 `PaperAgent.comment`。

Metadata 中 `research_id` 和 `thread_id` 都是研究轮次 ID，同一研究的恢复、修订和评论可以在 Threads 中关联；`work_id` 关联同一工作的不同研究轮次。还会记录 operation、输出格式，修订命令 ID 或评论执行 ID。并发研究使用独立上下文。

启用后会向指定 LangSmith 服务发送研究状态、模型消息以及能力输入/输出，可能包含论文、用户问题和数据摘录。运行时模型配置、API key 和数据库连接对象不作为自定义 trace 输入。Token 用量以供应商响应为准；价格未知的自定义模型不保证能自动计算费用。

接入前的历史执行不会自动出现在平台；仍从本地日志读取。恢复已有研究后会生成新 trace，并沿用已有研究 ID。

官方说明：[LangGraph 追踪](https://docs.langchain.com/langsmith/trace-with-langgraph)、[Threads](https://docs.langchain.com/langsmith/threads)。
