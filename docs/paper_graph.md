# 数模论文状态图流水线（v2）

`backend/ai_system/graph/` 用 LangGraph 状态图替换原来的单个 ReAct 主循环（MainAgent + ReviewAgent 续写）。
设置 `AI_PIPELINE=v2` 后，无模板的 Markdown 任务走这条流水线，其余情况仍用 MainAgent。

## 流程

```
analyze ──Send×N──▶ design ──▶ confirm ─┬─(有修改意见)──▶ design
                                        └─(通过)──▶ solve（按依赖分批并行）──▶ write ──▶ validate ─┬─▶ repair ──▶ validate
                                                                                                 └─▶ render
```

| 阶段 | 做什么 | 关键设计 |
|---|---|---|
| analyze | 固定代码读取 `data/`、`attachment/` 下的表格生成数据概览；模型拆出子问题 Q1…Qn 及依赖关系 | 数据概览不经过模型，每题一致 |
| design | 每个子问题并行：从方法库（`methods.py`，25 种常用数模方法）挑 2~4 种 → 生成 n 个候选方案 → 判分器按"贴题 / 可行 / 严谨"三项打分，选期望分最高的 | actor-critic；有 Jev key 时一次请求评完所有候选，否则用 LLM 结构化打分 |
| confirm | 可选的人工确认点（`AI_CONFIRM_MODELS=1`）：`interrupt` 把方案发给用户，回复"确认"继续，其他回复作为修改意见重新设计 | checkpoint 落盘，用户隔多久回复都能接着跑 |
| solve | 按依赖关系分批并行，每个子问题一个 CodeAct 循环 + 独立的持久 Python 内核 | 变量跨步保留；图自动保存；每次执行都是一条 run 记录 |
| write | 先并行写各子问题的建模求解章节，再写重述/分析/假设/符号/灵敏度/评价，最后写摘要 | 写作模型只看到结果登记表，数字只能写引用 |
| validate / repair | 代码校验：章节缺失、未知引用、正文出现未登记的小数/百分数、求解章节没引用结果或没插图；有问题的章节带着问题清单重写，最多 2 轮 | 校验不调模型，只修有问题的章节 |
| render | 引用替换为真实数值和图片，图按出现顺序编号，写出 `paper.md`；带引用的原稿留在 `.system/paper_raw.md` | 每个数字都能追溯到某次代码执行 |

## 结果登记表

求解时模型通过 `record_result(name, value, run_id, unit, description)` 登记结果。登记会检查：

- `run_id` 存在且执行成功；
- `value` 确实出现在这次执行的输出中（允许四舍五入到登记的位数、小数与百分数互换；至少两位有效数字才允许四舍五入）。

不满足就拒绝登记，模型必须先把结果 print 出来。图通过 `record_figure` 登记，并晋升到 `outputs/figures/F<n>.png`。
登记表存在 `.system/results.json`。

## 运行时文件

| 路径 | 内容 |
|---|---|
| `.system/graph.sqlite` | LangGraph checkpoint（AsyncSqliteSaver） |
| `.system/graph_thread.json` | 当前 thread 及状态（running / interrupted / done） |
| `.system/results.json` | 结果登记表 |
| `.system/validation.json` | 最近一轮校验问题 |
| `plan.md` / `plan.json` | 各阶段进度，前端固定展示 |

## 配置

| 环境变量 | 作用 |
|---|---|
| `AI_PIPELINE=v2` | 聊天接口启用状态图流水线 |
| `AI_CONFIRM_MODELS=1` | 建模方案需要用户确认 |
| `TYPESAFE_API_KEY` / `JEV_MODEL` | 用 Jev 评审候选方案（默认 `jev-1.14.0`） |

模型沿用工作的 brain / code / writing 配置：分析与设计用 brain，求解用 code，写作用 writing。

## 测试

`backend/tests/test_paper_graph.py` 用假模型离线跑完整条图（含 interrupt 恢复、sqlite checkpoint、登记拒绝编造值、校验后修复），
`backend/tests/test_kernel.py` 覆盖持久内核。
