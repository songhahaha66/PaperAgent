# Step 5 Preview 真实联调记录

起点：`origin/dev` 的 `60620dac7941fddadcf04540c29121627d23e582`。
原工作区的预置未提交文件保留在原处；在独立 dev 工作树完成本轮联调。

## 网关与运行控制

- 使用已有环境配置，未持久化、输出或提交凭据。
- HTTPS 证书校验开启。认证后的模型列表返回 21 个模型，选用实际 ID `step-5-preview`。
- 初始文本及强制工具调用均 HTTP 200，返回正确，分别约 1.9/2.9 秒，合计 344 tokens。
- 主流程请求 timeout=120 秒，SDK 重试=0，结构化修复最多 1 次；小探针 timeout=40 秒、重试=0。
- 默认推理配置在题目分析中耗尽 6,000-token 输出预算，只有思考内容，没有正文或工具参数。保留检查点后改用 `extra_body={"reasoning_effort":"low"}`，沿原研究继续。
- [Step 5 官方参数说明](https://platform.stepfun.ai/docs/en/guides/models/step-5-preview)支持 reasoning_effort；本轮未使用付费加速、其他模型平台或外部评审服务。

## 实际结果

问题：连续变量 x,y 非负，最大化 `3x+2y`，约束 `x+y<=4, x<=2, y<=3`。
真实模型执行 SciPy linprog，并将解代回所有原约束，通过可行域顶点枚举独立验证。
另用与 SciPy 无关的精确有理数交点计算复核：五个顶点的利润为 `0,6,9,6,10`，唯一最优点为 `x=2,y=2`，利润 `10`。

- 一个研究任务、一次求解尝试，最终 `completed`，任务 `accepted/current`，登记结果 12 项。
- 设计确认中断与恢复、真实内核计算、证据登记、结论审查、9 个章节写作、章节修复和原生 LaTeX/PDF 导出完成；未放宽验收。
- 线性规划静态技能及其 SciPy 官方来源确实出现在设计和求解提示中，由调用观察记录确认。
- 实际获取并核验 [SciPy linprog 官方资料](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.linprog.html)，通过已有题内参考资料登记路径进入参考文献；系统仍保留其书目核验提示。
- 真实模型生成“问题重述”润色候选，差异 611 字符。候选阶段 PDF 不变；应用后 PDF 更新，数学、数字及证据保护通过，求解尝试未重算。
- 中文 PDF 为 8 页。逐页检查后修复参考链接及长文本结果表越过页边的问题，通过已有章节候选/应用服务重新发布，最终 PDF 无页面外文本。保留修改前、润色后及最终版本。
- 从恢复设计到润色应用共约 420 秒、23 个真实模型请求。此时间不含前期探针、默认推理截断诊断和最后的确定性排版修复。

## 本轮定向修复

1. AIGW 的 SSE 事件重复提供累计 usage，并可能在最终快照前归零。普通逐块累加、甚至正差值累加都会高估。仅在该网关精确域名使用适配器，每个请求只汇入最终 usage 快照，保留文本与工具参数的原有流式输出。
2. 将该网关 Step 5 的输出预算映射为官方 `max_tokens`，避免仅发送 LangChain 的 `max_completion_tokens` 别名。
3. 原生 LaTeX 参考文献对 URL 使用 `\url{...}`，保留查询字符并允许换行，防止真实来源链接被页面裁切。
4. 内置模板加载 tabularx，并提示长文本表格使用页面宽度和 X 列；本次实际溢出的表格仅修改列格式后重新编译，全部文字、数字和公式保持原样。

最终适配器另经真实短探针验证：网关最终快照 30 输入 + 34 输出 = 64 tokens，与适配器一致。早期整段流水线的流式 token 汇总受旧累计逻辑影响，不作为真实用量或账单；本轮未查询费用。

## 覆盖与验证

- 模型流程调用生产 `PaperGraphAgent`、research-v3、真实内核与编辑服务，没有用 mock 模型冒充 E2E。
- 另通过真实 FastAPI workspace 路由的 HTTP ASGI transport，使用有效 JWT 和隔离的内存 SQLite 工作归属数据验证：未认证 401、越权 403、论文/技能/PDF 读取 200、重复应用幂等 200、旧版本候选 409。未模拟研究、模型或编辑逻辑。
- 相关回归 41 项通过；适配及链接修改后，受影响的 21 项定向检查再次通过。随后表格换行修改通过真实 PDF 编译及逐页边界检查验证。`git diff --check` 通过。
- PDF 目视检查覆盖求解页及参考文献页；章节差异、源文件、PDF、HTTP 检查和独立复核 JSON 留在本次环境的 `/workspace/paperagent-live-20261018/`。

未覆盖浏览器点击、WebSocket 全链路及生产部署。完整研究从后端入口运行，HTTP 检查仅覆盖上述 workspace 边界；不将其称为浏览器 E2E。

Cloud 环境原有 XeLaTeX 缺少中文包。采用 Debian 官方兼容包与 CTAN Fandol 字体，在临时 TEXMF 树补齐并验证；本次编译设置 `TEXMFHOME=/tmp/paperagent-texmf`、`TEXMFVAR=/tmp/paperagent-texmf-var`。仓库 Dockerfile 已包含中文 TeX 依赖，本轮未部署或修改系统级凭据。
