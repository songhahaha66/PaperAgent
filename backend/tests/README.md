# 测试范围

测试集保留核心用户流程和后果明确的回归：

- 研究启动、规划、真实代码执行、结果登记、论文生成和 Word/PDF 导出。
- 暂停、取消、重连、断点恢复，以及请求预算不被重试重置。
- 论文修改、审查、版本冲突和失败回滚，确保人工内容及已有结果不丢失。
- 证据来源、引用有效性、用户权限、路径边界和外部请求安全。

一个行为优先由一条完整流程验证。辅助工具、展示文案、内部结构和简单转换不单独铺开测试；新增回归应能说明具体故障和用户后果。权限与输入安全的参数化用例保留不同攻击路径。

## 场景索引

这些文件是入口示例，并非完整文件清单；有些场景跨越多个模块。

| 场景 | 主要检查 | 示例 |
| --- | --- | --- |
| 研究与计算 | 原始材料、研究问题、持续执行的 Python、结果与来源、后续计算 | `test_single_research_agent.py`、`test_paper_graph.py`、`test_context_sessions.py` |
| 中断与恢复 | 暂停、取消、重连、请求失败和上下文压缩后恢复；不重复执行代码或重置预算 | `test_execution_policy.py`、`test_compaction_recovery.py`、`test_research_delivery_recovery.py` |
| 沙箱与文件 | 执行记录、文件版本、上传下载失败、损坏输出、交付重试、沙箱切换 | `test_kernel.py`、`test_api_workspace_transfer.py`、`test_sandbox_factory.py` |
| 写作与导出 | Word/LaTeX/PDF、图表和引用、编辑审查、人工修改冲突、发布失败回滚 | `test_export.py`、`test_paper_interactions.py`、`test_scoped_paper_edits.py` |
| 权限与安全 | 用户和项目隔离、登录、路径穿越、符号链接、外部 URL 与凭据保护 | `test_passkey.py`、`test_workspace_routes.py`、`test_security_regressions.py`、`test_literature.py` |
| 产品状态与配置 | 研究进度、消息记录、个人经验、模型配置和请求/费用预算 | `test_conversation_timeline.py`、`test_experience_api.py`、`test_generation_budgets.py` |
| 前端工作区 | 标签和分屏、草稿与阅读位置、消息流、结果预览、配置和断线恢复 | `frontend/tests/workInteractions.test.cjs`、`conversationStream.test.cjs`、`workPreview.test.cjs` |

前端测试使用组件及模块测试环境，不是完整浏览器端到端测试。离线测试不能判断真实模型回答的正确性、远端沙箱部署或生产服务之间的连通性；这些需要另行验收。

共享测试搭建应放在 `tests/helpers/` 或 `conftest.py`，避免从其他 `test_*.py` 导入。测试必须能单独运行，不能依赖其他测试文件的收集顺序。

从 `backend/` 运行测试。安装沙箱 SDK 以覆盖文件 API 适配器；沙箱协议测试使用独立文件树的替身，不需要远端服务：

```sh
uv sync --locked --extra sandbox
uv run --locked --extra sandbox pytest -vv --tb=short --durations=10
```

单个测试（含 fixture 的创建和清理）最多运行 60 秒，超时会输出线程栈并终止 pytest，避免异步清理继续阻塞。CI 的测试步骤最多 10 分钟，整个 job 最多 15 分钟；同一分支或 PR 的新运行会取消旧运行。`-vv` 保留正在执行的测试名称，`--durations=10` 输出最慢用例。

模型响应使用测试替身；独立本地内核测试会实际运行 Python 子进程。应用工厂不再支持 `PAPERAGENT_SANDBOX_PROVIDER=local`，需要执行器的离线测试应显式注入替身。缺少 Pandoc 或 XeLaTeX 时，相应导出测试会跳过。

`test_process_jobs.py` 在单独的临时服务端目录运行生产任务监督器和受控 Python 片段，覆盖提交/查询回复丢失不重跑、重连补读不重复、超时信号升级、取消、日志限额、错误图片保存，以及 `code/common.py` 补丁回收。它不连接远端服务，也不会注册本机执行 provider。

`conftest.py` 为每个测试隔离后端状态目录，并禁止真实 HTTP transport；SDK 协议测试使用 MockTransport。研究集成测试显式声明 `offline_kernel`，只运行受控 fixture 代码并封存运行回执；编译测试显式声明 `offline_compiler`，通过独立目录的文件 API 替身传输后调用本地编译器。替身不会启用应用的本机执行兜底，也不替代真实沙箱部署验收。

`test_sandbox_factory.py` 用同一个已注册适配器走研究与编译入口，覆盖参数/标准输入传递、旧编译回执恢复、未知平台拒绝，以及切换平台（含不同远端工作目录）时不重跑或误用历史结果。编译替身应实现结构化 `exec(argv, stdin, timeout)` 并注入统一 provider registry，不应依赖 OpenSandbox 的 shell 字符串格式。

构造状态与证据路径请用 `workspace_path`、`WorkspaceFS.run_dir`，不要在公开项目目录直接写 `.system`、`runs` 或 `manifest.json`。成功结果 fixture 在登记前调用 `seal_run`，不能省略或绕过证据校验。旧项目版本门禁测试继续保留；模拟新项目的 API fixture 应明确设置 `architecture_version=2`。

从 `frontend/` 运行 `npm run test:workspace`，覆盖工作区交互、聊天恢复、文件编辑、草稿保存和资源清理。
