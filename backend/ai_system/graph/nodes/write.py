"""
阶段 5：按登记结果写作

先并行写每个子问题的"模型建立与求解"，再并行写重述、分析、假设、符号、灵敏度、评价和摘要。
用户上传了模板时，章节标题和顺序按模板（outline.py），模板里对不上的章节按模板标题单独撰写。
写作模型只看得到登记表，数字和图只能用 {{R:Rn}} / {{F:Fn}} 引用，杜绝编造数值。
"""

import asyncio
from typing import Dict, List, Tuple

from langgraph.runtime import Runtime

from ..context import GraphContext
from ..outline import template_outline
from .consistency import consistency_text
from ..llm_utils import as_messages, call_text, clip
from ..state import PaperState
from ..tracing import labelled

# (key, 标题)；solve:<id> 是每个子问题的求解章节，按子问题顺序展开
FRAME_SECTIONS: List[Tuple[str, str]] = [
    ("restatement", "一、问题重述"),
    ("analysis", "二、问题分析"),
    ("assumptions", "三、模型假设"),
    ("symbols", "四、符号说明"),
]
TAIL_SECTIONS: List[Tuple[str, str]] = [
    ("sensitivity", "六、灵敏度分析"),
    ("evaluation", "七、模型的评价与推广"),
]
SOLVE_HEADING = "五、模型的建立与求解"

STYLE = (
    "你是数学建模竞赛论文的执笔手，文风严谨、简洁，使用中文学术写作规范。\n"
    "硬性规则：\n"
    "1. 正文中的任何计算结果数字必须写成登记表里的引用，例如「预测准确率为 {{R:R3}}」，不要自己写数字；"
    "题目给定的常数、年份、编号可以直接写。\n"
    "2. 插图单独占一行写 {{F:F1}}，正文提到它时也写 {{F:F1}}，系统会编号。\n"
    "3. 公式用 LaTeX（行内 $...$，独立公式 $$...$$），公式中的符号可以写，但不要在公式里代入计算结果数字。\n"
    "4. 只输出本节正文，不要输出本节的标题，不要输出代码。小节标题用 ### 开头。"
)


def solve_key(subproblem_id: str) -> str:
    return f"solve:{subproblem_id}"


def section_plan(state: PaperState) -> List[Tuple[str, str, int]]:
    """全文章节顺序：(key, 标题, 标题级别)；有模板时用模板的章节标题和顺序"""
    top = [(item["key"], item["title"]) for item in state.get("outline") or []]
    if not top:
        top = [("abstract", "摘要")] + FRAME_SECTIONS + [("solve_heading", SOLVE_HEADING)] + TAIL_SECTIONS
    plan: List[Tuple[str, str, int]] = []
    for key, title in top:
        plan.append((key, title, 2))
        if key == "solve_heading":
            for sp in state["analysis"]["subproblems"]:
                plan.append((solve_key(sp["id"]), f"{sp['id']} {sp['title']}", 3))
    return plan


def _design_text(design: Dict) -> str:
    chosen = design["chosen"]
    return (
        f"方案：{chosen['name']}\n思路：{chosen['idea']}\n数学表述：{chosen['formulation']}\n"
        f"假设：{'；'.join(chosen.get('assumptions') or [])}\n检验：{chosen['validation']}"
    )


def _report_text(report: Dict) -> str:
    findings = "\n".join(
        f"- [{f['strength']}] {f['statement']}" if isinstance(f, dict) else f"- {f}"
        for f in report.get("key_findings") or []
    )
    limitations = "；".join(report.get("limitations") or [])
    return (
        f"概要：{report.get('summary', '')}\n实际方法：{report.get('approach', '')}\n主要结论：\n{findings}\n"
        f"灵敏度/检验：{report.get('sensitivity', '')}\n局限：{limitations}"
    )


def writing_sources(state: PaperState, subproblem: Dict) -> List[Dict]:
    """写作类子问题依据的子问题：声明的依赖，没有声明时用全部计算类子问题"""
    subproblems = state["analysis"]["subproblems"]
    deps = set(subproblem.get("depends_on") or [])
    return [sp for sp in subproblems if sp["id"] != subproblem["id"] and sp.get("kind") != "writing"
            and (not deps or sp["id"] in deps)]


# 评委每次都扣"实用性"（2024_C 三次 5.5-8.5）：结论诚实地说"转折不可预测"，但没有告诉读者能做什么。
# 建议要可执行，又不能夸大：显著的结论支撑"应当做什么"，不显著的结论支撑"不要据此做什么"，并给出替代做法
ACTIONABLE_ADVICE = (
    "建议写成读者可以直接照做的条目：在什么情境下、做什么、依据哪条结论（写明证据强度）。"
    "只有显著的结论可以支撑\"应当……\"；不显著或倾向性的结论用来说明\"不要因为……而……\"，"
    "同时给出不依赖该结论的替代做法（例如围绕已被证实的结构性因素安排策略、用描述性指标做赛中复盘）。"
    "不要把没有被证据支持的效应写成建议的依据。"
)


def subproblem_brief(state: PaperState, ctx: GraphContext, subproblem: Dict) -> str:
    sid = subproblem["id"]
    if subproblem.get("kind") == "writing":
        sources = "\n\n".join(subproblem_brief(state, ctx, sp) for sp in writing_sources(state, subproblem))
        return (
            f"## {sid} {subproblem['title']}（文书类交付物）\n问题：{subproblem['question']}\n"
            f"交付物：{'；'.join(subproblem.get('deliverables') or [])}\n"
            f"依据以下各问的结论和登记结果撰写，数字同样只能写引用。\n{ACTIONABLE_ADVICE}\n\n{sources}"
        )
    design = (state.get("designs") or {}).get(sid)
    report = (state.get("solutions") or {}).get(sid) or {}
    return (
        f"## {sid} {subproblem['title']}\n问题：{subproblem['question']}\n"
        + (_design_text(design) + "\n" if design else "")
        + f"求解报告：\n{_report_text(report)}\n登记结果：\n{ctx.registry.to_prompt_table(sid)}"
    )


def section_request(key: str, title: str, state: PaperState, ctx: GraphContext) -> str:
    """每一节都带上全文结论表和统一口径，各节结论口径一致"""
    request = _section_body(key, title, state, ctx)
    shared = consistency_text(ctx, state.get("consistency") or [])
    if shared:
        request = f"{request}\n\n{shared}"
    if ctx.template_requirements:
        request += (
            "\n\n# 用户模板的要求（章节标题和顺序已按模板安排，本节只写正文；措辞、格式、篇幅以模板为准）\n"
            f"{ctx.template_requirements}"
        )
    return request


def _section_body(key: str, title: str, state: PaperState, ctx: GraphContext) -> str:
    analysis = state["analysis"]
    subproblems = analysis["subproblems"]
    sections = state.get("sections") or {}

    if key.startswith("solve:"):
        sid = key.split(":", 1)[1]
        subproblem = next(sp for sp in subproblems if sp["id"] == sid)
        return (
            f"撰写「{title}」的建模与求解小节，一般依次包含：模型的建立（变量、公式推导）、求解过程与算法、"
            f"结果与图表、结果检验。如果这一问的交付物是备忘录、信件、建议书等文体，"
            f"就按交付物要求的文体和篇幅直接写成正文，不要套用上面的结构。"
            f"\n\n{subproblem_brief(state, ctx, subproblem)}"
        )

    if key == "restatement":
        return f"撰写「问题重述」，包括问题背景和需要解决的问题（逐条列出）。\n\n题目：\n{state['task']}"
    if key == "analysis":
        return (
            f"撰写「问题分析」，逐个子问题说明分析思路和选用模型的理由。\n\n整体思路：{analysis['overall_analysis']}\n\n"
            + "\n\n".join(
                f"{sp['id']} {sp['title']}\n" + _design_text(state["designs"][sp["id"]])
                for sp in subproblems if sp["id"] in (state.get("designs") or {})
            )
        )
    if key == "assumptions":
        extra = [a for sp in subproblems for a in (state["designs"].get(sp["id"], {}).get("chosen", {}).get("assumptions") or [])]
        return "撰写「模型假设」，合并去重后编号列出，每条附简短理由。\n\n" + "\n".join(
            f"- {a}" for a in list(analysis["assumptions"]) + extra
        )
    solved_text = "\n\n".join(clip(sections.get(solve_key(sp["id"]), ""), 4000) for sp in subproblems)
    if key == "symbols":
        return f"撰写「符号说明」，用 Markdown 表格（符号 | 说明 | 单位），只列下文实际用到的主要符号。\n\n求解章节：\n{solved_text}"
    if key == "sensitivity":
        return (
            "撰写「灵敏度分析」，汇总各子问题的检验和灵敏度分析结论，必须引用登记结果。\n\n"
            + "\n\n".join(subproblem_brief(state, ctx, sp) for sp in subproblems)
        )
    if key == "evaluation":
        return (
            "撰写「模型的评价与推广」，分优点、缺点、改进与推广三部分，结合各子问题的局限。"
            "推广部分写清模型在实际中怎么用：谁在什么场景下用哪个输出做什么决定，哪些输出因证据不足不能用于决策。\n\n"
            + "\n\n".join(subproblem_brief(state, ctx, sp) for sp in subproblems)
        )
    if key == "abstract":
        return (
            "撰写「摘要」：一段总述 + 每个子问题一段（方法 + 关键结果，关键结果必须引用登记结果）+ 一段总结，"
            "最后一行写「**关键词**：」并列出 4~6 个关键词。\n\n"
            f"论文标题：{analysis['title']}\n\n" + "\n\n".join(subproblem_brief(state, ctx, sp) for sp in subproblems)
        )
    if key.startswith("custom:"):
        guard = "只列出题目和数据来源，不要编造文献。" if any(w in title for w in ("参考文献", "文献", "引用")) else ""
        return (
            f"撰写用户模板要求的「{title}」一节，内容依据题目和各子问题的结论，必须引用登记结果。{guard}\n\n"
            f"题目：\n{clip(state['task'], 3000)}\n\n"
            + "\n\n".join(subproblem_brief(state, ctx, sp) for sp in subproblems)
        )
    raise KeyError(key)


async def write_section(ctx: GraphContext, state: PaperState, key: str, title: str) -> str:
    request = section_request(key, title, state, ctx)
    with labelled(key):
        return await call_text(ctx.writer_llm, as_messages(STYLE, request))


async def write_many(ctx: GraphContext, state: PaperState, items: List[Tuple[str, str]]) -> Dict[str, str]:
    texts = await asyncio.gather(*(write_section(ctx, state, key, title) for key, title in items))
    return dict(zip([key for key, _ in items], texts))


async def write(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    await ctx.set_stage("solve", "completed")
    await ctx.set_stage("write", "in_progress", "撰写求解章节")
    await ctx.events.stage("开始撰写论文")

    outline = template_outline(ctx.template_requirements) if ctx.template_requirements else []
    state = {**state, "outline": outline}
    plan = section_plan(state)
    solve_items = [(key, title) for key, title, _ in plan if key.startswith("solve:")]
    sections: Dict[str, str] = {}
    sections.update(await write_many(ctx, state, solve_items))

    # 摘要只依据各问的求解报告和登记结果，不读其他章节，和其余章节一起并行写，少一轮串行等待
    await ctx.set_stage("write", "in_progress", "撰写其余章节和摘要")
    with_solved = {**state, "sections": sections}
    rest = [(key, title) for key, title, _ in plan if key != "solve_heading" and not key.startswith("solve:")]
    sections.update(await write_many(ctx, with_solved, rest))
    await ctx.set_stage("write", "completed", f"{len(sections)} 个章节")
    log = f"write: {len(sections)} sections" + (f" (template outline: {len(outline)})" if outline else "")
    return {"sections": sections, "outline": outline, "log": [log]}
