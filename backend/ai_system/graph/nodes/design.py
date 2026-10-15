"""
阶段 2：建模方案（每个子问题一个并行分支）

1. 从方法库检索 2~4 种适用方法；
2. 基于这些方法生成 n 个候选方案（actor）；
3. 判分器按三条标准给每个方案打分（critic，有 Jev 时一次请求完成），选期望分最高的。

参考 MM-Agent 的 actor-critic 与 ModelingAgent 的"多候选 + 评审筛选"。
"""

import asyncio
import json
from typing import Dict, List

from langgraph.runtime import Runtime

from ...judge import Score
from ..context import GraphContext
from ..llm_utils import as_messages, call_structured
from ..methods import METHOD_INDEX, describe, methods_catalog
from ..state import BranchInput, CandidateSet, MethodPick

CRITERIA = {
    "fit": Score(
        instructions="这个建模方案是否直接、完整地回答了子问题的要求，交付物是否都能产出？",
        criteria=[
            "答非所问或遗漏主要交付物",
            "只回答了一部分",
            "基本回答，个别交付物不清楚",
            "完整回答，交付物明确",
            "完整回答且对问题有深入理解",
        ],
    ),
    "feasibility": Score(
        instructions="根据数据概览，这个方案能否只用已给数据和常见 Python 库（pandas、numpy、scipy、sklearn、statsmodels、networkx）在一个下午内实现？",
        criteria=[
            "依赖不存在的数据或无法实现",
            "需要大量额外数据或假设",
            "可实现但步骤含糊",
            "可实现，步骤清楚",
            "可实现且每一步都对应到具体数据列",
        ],
    ),
    "data_use": Score(
        instructions="方案是否充分利用了题目给出的全部相关数据（例如全部比赛、全部年份），而不是只用其中一小部分？",
        criteria=[
            "基本不用题目数据",
            "只用了很小一部分数据且没有理由",
            "用了部分数据，理由一般",
            "用了大部分相关数据",
            "充分利用全部相关数据，并说明各部分数据的用途",
        ],
    ),
    "rigor": Score(
        instructions="方案的数学表述是否严谨，是否包含结果检验和灵敏度分析？",
        criteria=[
            "没有数学表述",
            "表述粗糙，无检验",
            "有表述，检验较弱",
            "表述清楚，有检验或灵敏度分析",
            "表述严谨，检验和灵敏度分析都具体可执行",
        ],
    ),
}

ACTOR_SYSTEM = (
    "你是数学建模竞赛的建模手。请为给定子问题设计几种不同思路的建模方案，"
    "方案要能用 Python 在给定数据上实现，步骤具体到用哪些数据、算什么量。"
    "不同候选之间要有实质差异（不同方法或不同建模角度），不要只换说法。"
)


def _subproblem_text(subproblem: Dict) -> str:
    deliverables = "；".join(subproblem.get("deliverables") or []) or "（未列出）"
    return f"{subproblem['id']} {subproblem['title']}\n问题：{subproblem['question']}\n交付物：{deliverables}"


async def pick_methods(ctx: GraphContext, branch: BranchInput) -> List[str]:
    prompt = (
        f"# 子问题\n{_subproblem_text(branch['subproblem'])}\n\n"
        f"# 数据概览\n{branch['data_profile']}\n\n"
        f"# 方法库\n{methods_catalog()}\n\n"
        "从方法库中挑选 2~4 个最适合这个子问题的方法 key（可以组合使用）。"
    )
    pick = await call_structured(ctx.planner_llm, MethodPick, as_messages(ACTOR_SYSTEM, prompt))
    keys = [k for k in pick.method_keys if k in METHOD_INDEX]
    return keys[:4]


async def propose(ctx: GraphContext, branch: BranchInput, method_keys: List[str]) -> List[Dict]:
    analysis = branch["analysis"]
    feedback = branch.get("feedback") or ""
    prompt = (
        f"# 题目整体分析\n{analysis['overall_analysis']}\n\n"
        f"# 子问题\n{_subproblem_text(branch['subproblem'])}\n\n"
        f"# 数据概览\n{branch['data_profile']}\n\n"
        f"# 参考方法\n{describe(method_keys) or '（自行选择）'}\n\n"
        + (f"# 用户对上一版方案的意见（必须采纳）\n{feedback}\n\n" if feedback else "")
        + f"请给出 {ctx.n_candidates} 个候选方案。"
    )
    result = await call_structured(ctx.planner_llm, CandidateSet, as_messages(ACTOR_SYSTEM, prompt))
    candidates = [c.model_dump() for c in result.candidates][: ctx.n_candidates]
    if not candidates:
        raise RuntimeError(f"{branch['subproblem']['id']} 没有生成任何候选方案")
    return candidates


async def critique(ctx: GraphContext, branch: BranchInput, candidates: List[Dict]) -> List[Dict]:
    """返回每个候选的各项期望分（0~4）与总分；没有判分器时全部记 None 并选第一个"""
    if ctx.judge is None or len(candidates) == 1:
        return [{"total": None} for _ in candidates]
    # 状态只放判断需要的内容，过滤掉无关字段，减少判分噪声
    state = {
        "子问题": _subproblem_text(branch["subproblem"]),
        "数据概览": branch["data_profile"][:6000],
        "候选方案": {f"方案{i + 1}": c for i, c in enumerate(candidates)},
    }
    # 每个候选单独一次请求（四项一起问），候选之间并行。所有候选放进同一次请求时，
    # 实测 DeepSeek 会给三个候选打出完全相同的分；逐项逐候选分开问又要 12 次请求
    async def judge_one(i: int) -> Dict:
        questions = {
            key: Score(instructions=f"只评价「方案{i + 1}」。{question.instructions}", criteria=question.criteria)
            for key, question in CRITERIA.items()
        }
        answers = await ctx.judge.ask(state, questions)
        item = {key: answers.scores[key].score for key in CRITERIA}
        item["total"] = round(sum(item.values()), 3)
        return item

    return list(await asyncio.gather(*(judge_one(i) for i in range(len(candidates)))))


WRITING_DESIGN = {
    "name": "基于前序结论撰写",
    "method_keys": [],
    "idea": "本问是文书类交付物，不需要新的计算，直接依据前面各问的登记结果和结论撰写。",
    "formulation": "",
    "steps": [],
    "assumptions": [],
    "validation": "与前序结论保持一致",
}


# 候选方案生成多次失败时的退路：交给求解者结合数据自行确定方法，
# 一个子问题的设计失败不应让整篇论文中止
SELF_DESIGN = {
    "name": "求解时自行设计",
    "method_keys": [],
    "idea": "候选方案生成失败，由求解者根据子问题要求和数据先做探索，再自行确定建模方法。",
    "formulation": "",
    "steps": ["探索相关数据列", "选择并说明建模方法", "求解并检验", "灵敏度分析"],
    "assumptions": [],
    "validation": "给出检验结果和灵敏度分析",
}


async def design(branch: BranchInput, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    subproblem = branch["subproblem"]
    await ctx.set_stage("design", "in_progress")
    if subproblem.get("kind") == "writing":
        # 写作类子问题（备忘录、建议信等）不需要建模方案
        return {
            "designs": {subproblem["id"]: {
                "methods": [], "candidates": [WRITING_DESIGN], "scores": [{"total": None}],
                "chosen": WRITING_DESIGN, "chosen_index": 0, "critic": None, "critic_error": None,
            }},
            "log": [f"design {subproblem['id']}: writing"],
        }
    await ctx.events.stage(f"{subproblem['id']}：检索方法并设计候选方案")

    try:
        method_keys = await pick_methods(ctx, branch)
    except Exception:  # 方法检索失败时不给参考方法，候选方案照常生成
        method_keys = []
    try:
        candidates = await propose(ctx, branch, method_keys)
    except Exception as exc:
        await ctx.events.emit("paper_design", f"{subproblem['id']} 候选方案生成失败，改由求解时自行设计")
        return {
            "designs": {subproblem["id"]: {
                "methods": method_keys, "candidates": [SELF_DESIGN], "scores": [{"total": None}],
                "chosen": SELF_DESIGN, "chosen_index": 0, "critic": None, "critic_error": None,
                "design_error": str(exc),
            }},
            "log": [f"design {subproblem['id']}: fallback ({exc})"],
        }
    try:
        scores = await critique(ctx, branch, candidates)
        critic_error = None
    except Exception as exc:  # 判分失败不阻塞流程，退回第一个候选
        scores = [{"total": None} for _ in candidates]
        critic_error = str(exc)

    totals = [s["total"] if s["total"] is not None else -1 for s in scores]
    chosen_index = max(range(len(candidates)), key=lambda i: totals[i])
    chosen = candidates[chosen_index]

    summary = f"{subproblem['id']} 选用「{chosen['name']}」"
    if scores[chosen_index]["total"] is not None:
        summary += f"（评审 {scores[chosen_index]['total']:.1f}/{4 * len(CRITERIA)}，候选 {len(candidates)} 个）"
    await ctx.events.emit("paper_design", summary)

    return {
        "designs": {
            subproblem["id"]: {
                "methods": method_keys,
                "candidates": candidates,
                "scores": scores,
                "chosen": chosen,
                "chosen_index": chosen_index,
                "critic": getattr(ctx.judge, "label", None) or getattr(ctx.judge, "name", None),
                "critic_error": critic_error,
            }
        },
        "log": [f"design {subproblem['id']}: {json.dumps(totals)} -> {chosen_index}"],
    }
