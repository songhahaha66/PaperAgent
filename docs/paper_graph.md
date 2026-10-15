# 数模论文状态图流水线（v2）

`backend/ai_system/graph/` 用 LangGraph 状态图替换原来的单个 ReAct 主循环（MainAgent + ReviewAgent 续写）。
设置 `AI_PIPELINE=v2` 后，无模板的 Markdown 任务走这条流水线，其余情况仍用 MainAgent。

## 流程

```
analyze ──Send×N──▶ design ──▶ confirm ─┬─(有修改意见)──▶ design
                                        └─(通过)──▶ solve（按依赖分批并行：explore → 正式求解 → 审查）──▶ reconcile ──▶ write ──▶ validate ─┬─▶ repair ──▶ validate
                                                                                                 └─▶ render
```

| 阶段 | 做什么 | 关键设计 |
|---|---|---|
| analyze | 固定代码读取 `data/`、`attachment/` 下的表格生成数据概览；模型拆出子问题 Q1…Qn 及依赖关系 | 数据概览不经过模型，每题一致 |
| design | 每个子问题并行：从方法库（`methods.py`，25 种常用数模方法）挑 2~4 种 → 生成 n 个候选方案 → 判分器按"贴题 / 可行 / 数据利用 / 严谨"四项打分，选期望分最高的 | actor-critic；有 Jev key 时一次请求评完所有候选，否则用 LLM 结构化打分 |
| confirm | 可选的人工确认点（`AI_CONFIRM_MODELS=1`）：`interrupt` 把方案发给用户，回复"确认"继续，其他回复作为修改意见重新设计 | checkpoint 落盘，用户隔多久回复都能接着跑 |
| solve · explore | 前两名评审总分差距不超过 2 分、且能定出统一数值指标时，评分最高的 k 个候选（默认 2）先在数据上试跑（否则直接按评审选择求解，省下试跑成本）：先定统一验证协议（同一指标、同一划分、同一基线），各候选开独立内核并行跑一个短循环（默认 12 步），按协议汇报指标；指标要通过与结果登记相同的核对才参与比较。有可比指标按指标选（1% 内打平用评审分），没有统一指标时按 critic 对执行证据的评分选 | 借鉴 AIDE / MLE-bench 的执行驱动搜索；胜出分支的内核和对话历史整体晋升为正式求解，探索的计算不浪费；记录在 `designs[Qn].exploration` |
| solve | 写作类子问题（备忘录、建议信等，analyze 标为 `kind=writing`）不设计方案、不开内核，写作时直接依据前序结论和登记结果撰写；计算类按依赖关系分批并行，每个子问题一个 CodeAct 循环 + 独立的持久 Python 内核；交报告后由审查模型检查数据范围、泄漏、过拟合、指标选择、结论是否被支持，必须修正的问题交回同一循环返工（默认 1 轮） | 变量跨步保留；早期代码和输出按 10 步一块压缩（`compaction.py`），至少保留最近 6 步原文，块内前缀不变以命中缓存 |
| reconcile | 各子问题报告的主要结论登记为结论节点（陈述 + 证据强度 显著/倾向/不显著 + 指向登记结果的证据）；审查模型通读结论表，找出跨子问题矛盾和强度与证据不符的结论，给出统一口径 | 结论图：登记表从"数字可追溯"扩展到"结论可追溯"；统一口径写进 `.system/consistency.json` |
| write | 先并行写各子问题的建模求解章节，再写重述/分析/假设/符号/灵敏度/评价，最后写摘要 | 写作模型只看到结果登记表，数字只能写引用；每一节都带着结论表和统一口径 |
| validate / repair | 代码校验：章节缺失、未知引用、正文出现未登记的小数/百分数、求解章节没引用结果或没插图；再用一次模型调用检查摘要、问题分析、灵敏度、评价是否与结论表一致；有问题的章节带着问题清单重写，最多 2 轮 | 数字校验不调模型；只修有问题的章节 |
| render | 引用替换为真实数值和图片，图按出现顺序编号，写出 `paper.md`；带引用的原稿留在 `.system/paper_raw.md` | 每个数字都能追溯到某次代码执行 |

## 结果登记表

求解时模型通过 `record_results([{name, value, run_id, unit, description}, ...])` 批量登记结果（一次调用登记一批，减少往返）。每条登记会检查：

- `run_id` 存在且执行成功；
- `value` 确实出现在这次执行的输出中（允许四舍五入到登记的位数、小数与百分数互换；至少两位有效数字才允许四舍五入）。

不满足就拒绝登记，模型必须先把结果 print 出来。图通过 `record_figure` 登记，并晋升到 `outputs/figures/F<n>.png`。
登记表存在 `.system/results.json`。

## 运行时文件

| 路径 | 内容 |
|---|---|
| `.system/graph.sqlite` | LangGraph checkpoint（AsyncSqliteSaver） |
| `.system/graph_thread.json` | 当前 thread 及状态（running / interrupted / done） |
| `.system/results.json` | 结果登记表（`results`）与结论节点（`claims`） |
| `.system/consistency.json` | 结论冲突与统一口径 |
| `.system/validation.json` | 最近一轮校验问题 |
| `plan.md` / `plan.json` | 各阶段进度，前端固定展示 |

结构化输出第一次用工具调用；参数不合 schema（如候选方案给了空列表）或模型没调用工具时，重试改为要纯文本 JSON 再校验。
某个子问题的候选方案多次生成失败时，不中止整篇，改用"求解时自行设计"的方案交给求解循环，失败原因记在 `designs[Qn].design_error`。

外部原因（网关故障、账号受限）让子问题求解失败时，`PaperGraphAgent.resume_failed()` 回到这些子问题开始求解之前的 checkpoint 重放，
先清掉它们失败时登记的结果；已完成的子问题不重跑。评测里对应 `python -m evals run --pipeline v2 --resume`。

## 2024_C 首轮评测发现的问题与对应改动

| 现象 | 改动 |
|---|---|
| 一题 56 分钟、534 万 token；输入 496 万中 90% 命中前缀缓存，但长循环会撑满上下文 | 按整块推进的上下文压缩（块内前缀不变，不破坏缓存）；评测记录 cache_read |
| Q1–Q3 只用了决赛一场数据；Q3 正样本 62.6% 时用 F1 显得偏高 | 结果审查节点；评审新增"数据利用"；求解提示要求全量数据、防泄漏、给基线 |
| Q3 模型用文字收尾、没交报告 | 提醒一次补交报告 |
| "68.7%%"、"-6.26 点 点"、"0.2320 p值"、公式内带单位 | 渲染去重单位、公式内只放数值；单位只填物理/计数单位 |
| 求解章节小标题与章节标题同级；备忘录被套成建模模板 | 小标题自动降级；按交付物文体写作 |

## 配置

| 环境变量 | 作用 |
|---|---|
| `AI_PIPELINE=v2` | 聊天接口启用状态图流水线 |
| `AI_CONFIRM_MODELS=1` | 建模方案需要用户确认 |
| `AI_EXPLORE_TOPK` | 求解前试跑几个候选方案（默认 2，设为 1 关闭探索） |
| `TYPESAFE_API_KEY` / `JEV_MODEL` | 用 Jev 评审候选方案（默认 `jev-1.14.0`） |

模型沿用工作的 brain / code / writing 配置：分析与设计用 brain，求解用 code，写作用 writing。

## 测试

`backend/tests/test_paper_graph.py` 用假模型离线跑完整条图（含 interrupt 恢复、sqlite checkpoint、登记拒绝编造值、校验后修复），
`backend/tests/test_kernel.py` 覆盖持久内核。
