# 测试范围

测试集保留核心用户流程和后果明确的回归：

- 研究启动、规划、真实代码执行、结果登记、论文生成和 Word/PDF 导出。
- 暂停、取消、重连、断点恢复，以及请求预算不被重试重置。
- 论文修改、审查、版本冲突和失败回滚，确保人工内容及已有结果不丢失。
- 证据来源、引用有效性、用户权限、路径边界和外部请求安全。

一个行为优先由一条完整流程验证。辅助工具、展示文案、内部结构和简单转换不单独铺开测试；新增回归应能说明具体故障和用户后果。权限与输入安全的参数化用例保留不同攻击路径。

从 `backend/` 运行测试。安装沙箱 SDK 以覆盖文件 API 适配器；沙箱协议测试使用独立文件树的替身，不需要远端服务：

```sh
uv sync --locked --extra sandbox
uv run --locked --extra sandbox pytest -vv --tb=short --durations=10
```

单个测试（含 fixture 的创建和清理）最多运行 60 秒，超时会输出线程栈并终止 pytest，避免异步清理继续阻塞。CI 的测试步骤最多 10 分钟，整个 job 最多 15 分钟；同一分支或 PR 的新运行会取消旧运行。`-vv` 保留正在执行的测试名称，`--durations=10` 输出最慢用例。

模型响应使用测试替身；独立本地内核测试会实际运行 Python 子进程。应用工厂不再支持 `PAPERAGENT_SANDBOX_PROVIDER=local`，需要执行器的离线测试应显式注入替身。缺少 Pandoc 或 XeLaTeX 时，相应导出测试会跳过。

`conftest.py` 为每个测试隔离后端状态目录，并禁止真实 HTTP transport；SDK 协议测试使用 MockTransport。研究集成测试显式声明 `offline_kernel`，只运行受控 fixture 代码并封存运行回执；编译测试显式声明 `offline_compiler`，通过独立目录的文件 API 替身传输后调用本地编译器。替身不会启用应用的本机执行兜底，也不替代真实沙箱部署验收。

`test_sandbox_factory.py` 用同一个已注册适配器走研究与编译入口，覆盖参数/标准输入传递、旧编译回执恢复、未知平台拒绝，以及切换平台（含不同远端工作目录）时不重跑或误用历史结果。编译替身应实现结构化 `exec(argv, stdin, timeout)` 并注入统一 provider registry，不应依赖 OpenSandbox 的 shell 字符串格式。

构造状态与证据路径请用 `workspace_path`、`WorkspaceFS.run_dir`，不要在公开项目目录直接写 `.system`、`runs` 或 `manifest.json`。成功结果 fixture 在登记前调用 `seal_run`，不能省略或绕过证据校验。旧项目版本门禁测试继续保留；模拟新项目的 API fixture 应明确设置 `architecture_version=2`。

从 `frontend/` 运行 `npm run test:workspace`，覆盖工作区交互、聊天恢复、文件编辑、草稿保存和资源清理。
