# 端到端评测（MM-Bench）

用来回答"改了 agent 之后论文到底有没有变好"。每次改流程、改 prompt、换模型前后各跑一次，对比 `summary.md`。

## 准备

1. 克隆 [LLM-MM-Agent](https://github.com/usail-hkust/LLM-MM-Agent)，只需要其中的 `MMBench/problem` 与 `MMBench/dataset`（CC BY-NC 4.0，不随本仓库分发）。
2. 生成模型（被评测的 agent 用）：`EVAL_MODEL`、`EVAL_API_KEY`，可选 `EVAL_PROVIDER`（默认 `openai`）、`EVAL_BASE_URL`。
3. 判分（二选一）：
   - Jev：`TYPESAFE_API_KEY`。默认固定 `jev-1.14.0`，前后对比时不要换版本。
   - LLM：`EVAL_JUDGE_MODEL`、`EVAL_JUDGE_API_KEY` 等。应与生成模型不同，避免自评偏高。

## 运行

```bash
cd backend
uv run python -m evals all --bench ~/LLM-MM-Agent/MMBench --out eval_runs/baseline
# 只重新判分 / 只重新汇总
uv run python -m evals score  --bench ... --out eval_runs/baseline --judge jev
uv run python -m evals report --bench ... --out eval_runs/baseline
```

默认跑 10 题子集（`evals/mmbench.py` 的 `DEFAULT_SUBSET`：7 道带数据的 C/D 题 + 3 道纯建模题），`--problems 2024_C,2023_C` 可指定。

## 指标

| 指标 | 怎么算 | 来源 |
|---|---|---|
| 总分与 4 个维度（1–10） | MM-Bench 的问题分析、建模严谨性、实用与科学性、结果与偏差分析，各 2 个子项；每个子项是一个 5 级 Score 问题，期望等级映射回 1–10 | Jev 或 LLM 判分 |
| 代码成功率 | `manifest.json` 中 runs 的 success / 总数 | 确定性 |
| 章节齐全 | 8 个数模标准章节在标题中出现的比例 | 确定性 |
| 数字可追溯 | 论文中的小数/百分数能在 `runs/`、`outputs/` 的代码输出里找到的比例；题目原文里的数字不计 | 确定性 |
| 耗时、tokens | 运行墙钟时间；LangChain usage 回调汇总 | 运行记录 |

Jev 判分会给出每个子项的置信度，低于 0.5 的列在 `summary.md` 末尾，建议人工复核。Jev 的分数与 MM-Bench 论文里 GPT-4o 的分数不是同一把尺子，只适合同一套配置下前后对比。
