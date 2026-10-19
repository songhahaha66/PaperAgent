"""
阶段 2：建模方案（每个子问题一个并行分支）

1. 从方法库检索 2~4 种适用方法；
2. 基于这些方法生成 n 个候选方案（actor）；
3. 判分器按三条标准给每个方案打分（critic，有 Jev 时一次请求完成），选期望分最高的。

参考 MM-Agent 的 actor-critic 与 ModelingAgent 的"多候选 + 评审筛选"。
"""

import asyncio
import json
from typing import Dict, List, Tuple

from langgraph.runtime import Runtime

from ...judge import Score
from ..context import GraphContext
from ..llm_utils import as_messages, call_structured
from ..methods import METHOD_INDEX, describe, methods_catalog, allowed_method_keys, skill_policy_prompt, SKILL_SOURCES, SKILL_VERSION
from ..state import BranchInput, CandidateSet, MethodPick
from ..tracing import labelled
from ...research.assumptions import unbound_assumptions

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
    "严格限于当前子问题的交付，不提前完成下游问，也不为数据整理附加预测、ANOVA或其他假设检验。"
    "用户已指定方法时按指定方法设计；只有存在真实未定路线时才需要多方案，不能为了候选数量扩大研究范围。"
)


def _subproblem_text(subproblem: Dict) -> str:
    deliverables = "；".join(subproblem.get("deliverables") or []) or "（未列出）"
    return f"{subproblem['id']} {subproblem['title']}\n问题：{subproblem['question']}\n交付物：{deliverables}"


async def pick_methods(ctx: GraphContext, branch: BranchInput) -> List[str]:
    allowed=sorted(allowed_method_keys(ctx.skill_policy))
    if ctx.skill_policy and ctx.skill_policy['mode']=='selected' and len(allowed)==1:
        return allowed  # The user already made this choice; no model discovery is needed.
    prompt = (
        f"# 原题\n{branch['source']}\n\n"
        f"# 子问题\n{_subproblem_text(branch['subproblem'])}\n\n"
        f"# 数据概览\n{branch['data_profile']}\n\n"
        f"# 已登记的要求与假设\n{json.dumps(branch.get('research_contract', {}), ensure_ascii=False)}\n\n"
        f"# 用户技能配置\n{skill_policy_prompt(ctx.skill_policy)}\n\n"
        f"# 方法库\n{methods_catalog(ctx.skill_policy)}\n\n"
        "从方法库中挑选 2~4 个最适合这个子问题的方法 key（可以组合使用）。"
    )
    pick = await call_structured(ctx.planner_llm, MethodPick, as_messages(ACTOR_SYSTEM, prompt))
    keys = [k for k in dict.fromkeys(pick.method_keys) if k in allowed_method_keys(ctx.skill_policy)]
    if not keys and ctx.skill_policy and ctx.skill_policy['mode'] == 'selected':
        keys = sorted(allowed_method_keys(ctx.skill_policy))
    return keys[:4]


async def propose(ctx: GraphContext, branch: BranchInput, method_keys: List[str]) -> List[Dict]:
    from .explore import two_candidate_mode
    paired=two_candidate_mode(ctx,branch)
    count=2 if paired else ctx.n_candidates
    analysis = branch["analysis"]
    feedback = branch.get("feedback") or ""
    from .solve import _dependency_text  # 避免循环导入

    # 按需设计时前序子问题已经求解完，方案可以建立在它们的实际结论上
    dependency_text = _dependency_text(branch.get("dependencies") or {}, ctx)
    from ...research.citations import literature_root
    from ...research.literature import literature_context
    from ...research.experience import prompt_context
    prompt = (
        f"# 原题（含题内数据）\n{branch['source']}\n\n"
        f"# 题目整体分析\n{analysis['overall_analysis']}\n\n"
        f"# 子问题\n{_subproblem_text(branch['subproblem'])}\n\n"
        f"# 数据概览\n{branch['data_profile']}\n\n"
        f"# 已登记的要求、假设及任务作用范围\n{json.dumps(branch.get('research_contract', {}), ensure_ascii=False)}\n\n"
        f"# 用户技能配置\n{skill_policy_prompt(ctx.skill_policy)}\n\n"
        f"# 参考方法\n{describe(method_keys, ctx.skill_policy) or '（在允许范围内自行选择）'}\n\n"
        + literature_context(literature_root(ctx), _subproblem_text(branch['subproblem'])) + '\n\n'
        + prompt_context(ctx,branch,method_keys,'design') + '\n\n'
        + (f"{dependency_text}\n\n" if dependency_text else "")
        + (f"# 用户对上一版方案的意见（必须采纳）\n{feedback}\n\n" if feedback else "")
        + ("当前为双方案监督学习比较，必须恰好给出两项实质不同的方案，不得只改名称。" if paired else f"请给出最多 {count} 个候选方案，直接执行已明确的方法即可，不凑数。")
        + "assumptions只列实际依赖的新增关键前提，使用稳定key、statement和可执行checks；已有前提沿用契约，不重复创建。"
    )
    def validate_candidates(result):
        if paired:
            from ...research.store import digest
            if len(result.candidates)!=2 or len({digest({k:v for k,v in c.model_dump().items() if k!='name'}) for c in result.candidates})!=2:
                raise ValueError('双方案比较需要恰好两项不同方案')
        for candidate in result.candidates:
            if not set(candidate.method_keys) <= allowed_method_keys(ctx.skill_policy):
                raise ValueError('候选方案使用了被禁用或未允许的方法')
            if ctx.skill_policy and ctx.skill_policy['mode'] == 'selected' and not candidate.method_keys:
                raise ValueError('候选方案必须明确标记所选技能；若不适用请说明缺口')
            unbound_assumptions(branch["subproblem"]["id"], candidate.assumptions, branch.get("research_contract", {}))

    result = await call_structured(ctx.planner_llm, CandidateSet, as_messages(ACTOR_SYSTEM, prompt), validate=validate_candidates)
    candidates = [c.model_dump() for c in result.candidates][:count]
    from ...research.citations import audit_citations, citation_issues
    from ...research.literature import CITATION
    for candidate in candidates:
        text='\n'.join([candidate['idea'],candidate['formulation'],candidate['validation'],*candidate['steps']])
        if CITATION.search(text):
            await audit_citations(literature_root(ctx),[text],ctx.planner_llm)
            issues=citation_issues(literature_root(ctx),text)
            if issues:
                raise ValueError('设计中的文献引用未获支持：'+'；'.join(issues))
    if not candidates:
        raise RuntimeError(f"{branch['subproblem']['id']} 没有生成任何候选方案")
    return candidates


async def critique(ctx: GraphContext, branch: BranchInput, candidates: List[Dict]) -> List[Dict]:
    """返回每个候选的各项期望分（0~4）与总分；没有判分器时全部记 None 并选第一个"""
    from .explore import two_candidate_mode
    if two_candidate_mode(ctx,branch):
        # Both implementations must run on the frozen split. Prose scoring
        # cannot select their winner and would spend budget before measurement.
        return [{"total":None,"basis":"pending_project_measurement"} for _ in candidates]
    if ctx.judge is None or len(candidates) == 1:
        return [{"total": None} for _ in candidates]
    # 状态只放判断需要的内容，过滤掉无关字段，减少判分噪声
    state = {
        "原题": branch["source"],
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


async def make_design(ctx: GraphContext, branch: BranchInput) -> Tuple[Dict, str]:
    """为一个子问题产出方案记录，返回 (记录, 日志)"""
    with labelled(f"{branch['subproblem']['id']}:design"):
        record, log = await _make_design(ctx, branch)
        keys = record['chosen'].get('method_keys') or []
        record['skills'] = [{'key': key, 'version': SKILL_VERSION} for key in keys if key in SKILL_SOURCES]
        from ...research.literature import CITATION
        record['literature_refs']=sorted(set(CITATION.findall(json.dumps(record.get('chosen',{}),ensure_ascii=False))))
        if ctx.experience_order is not None:
            record['experience_refs']=[r for r in ctx.experience_order.experience_refs if r['stage']=='design']
        elif ctx.research is not None:
            from ...research.experience import design_usage
            used=design_usage(ctx.research,branch['subproblem']['id'])
            if used:record['experience_refs']=used[0]['refs'];record['experience_files']=used[0]['file_inputs']
        return record, log


async def _make_design(ctx: GraphContext, branch: BranchInput) -> Tuple[Dict, str]:
    subproblem = branch["subproblem"]
    if subproblem.get("kind") == "writing":
        # 写作类子问题（备忘录、建议信等）不需要建模方案
        record = {"methods": [], "candidates": [WRITING_DESIGN], "scores": [{"total": None}],
                  "chosen": WRITING_DESIGN, "chosen_index": 0, "critic": None, "critic_error": None}
        return record, f"design {subproblem['id']}: writing"
    await ctx.events.stage(f"{subproblem['id']}：检索方法并设计候选方案")

    try:
        method_keys = await pick_methods(ctx, branch)
    except Exception:  # 方法检索失败时不给参考方法，候选方案照常生成
        method_keys = sorted(allowed_method_keys(ctx.skill_policy))[:4] if ctx.skill_policy and ctx.skill_policy['mode'] == 'selected' else []
    try:
        candidates = await propose(ctx, branch, method_keys)
    except Exception as exc:
        await ctx.events.emit("paper_design", f"{subproblem['id']} 候选方案生成失败，改由求解时自行设计")
        fallback = {**SELF_DESIGN, 'method_keys': method_keys}
        record = {"methods": method_keys, "candidates": [fallback], "scores": [{"total": None}],
                  "chosen": fallback, "chosen_index": 0, "critic": None, "critic_error": None,
                  "design_error": str(exc)}
        return record, f"design {subproblem['id']}: fallback ({exc})"
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
    record = {
        "methods": method_keys,
        "candidates": candidates,
        "scores": scores,
        "chosen": chosen,
        "chosen_index": chosen_index,
        "critic": getattr(ctx.judge, "label", None) or getattr(ctx.judge, "name", None),
        "critic_error": critic_error,
    }
    return record, f"design {subproblem['id']}: {json.dumps(totals)} -> {chosen_index}"


async def design(branch: BranchInput, runtime: Runtime[GraphContext]) -> dict:
    """需要人工确认方案时，全部子问题先并行设计，再统一交给 confirm"""
    ctx = runtime.context
    await ctx.set_stage("design", "in_progress")
    record, log = await make_design(ctx, branch)
    return {"designs": {branch["subproblem"]["id"]: record}, "log": [log]}
