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
    "1. 正文中的任何计算结果数字必须使用本节可用引用表中的真实引用，不要自己写数字或猜测编号；"
    "题目给定的常数、年份、编号可以直接写。\n"
    "2. 只有本节可用引用表中确有图表时才能插图。复制表中的图引用独占一行，正文指代同一图也使用该引用，系统会编号。没有图表时不得写图引用或声称见图。\n"
    "3. 公式使用 LaTeX 语法，公式中的符号可以写，但不要在公式里代入计算结果数字。\n"
    "4. 论文是给评委读的：不要出现代码里的变量名、列名和程序输出的原样字符串（如 word_length、valAUC、n_repeat），"
    "改写成中文名称（如\"单词长度\"）。只引用支撑结论的关键结果，多个同类结果用表格对比，不要在一句话里罗列一串统计量。"
)


def writing_style(ctx: GraphContext) -> str:
    if ctx.output_mode == "latex":
        return STYLE + (
            "\n输出格式：原生 LaTeX 章节源码，直接用于 XeLaTeX 编译。"
            "只输出本节正文，不要代码围栏、Markdown 标题、加粗或管道表格；"
            "不要输出导言区、本节标题、document 环境、自定义宏或文件操作。"
            "小节使用 \\subsubsection，列表使用 itemize/enumerate，表格使用 tabular/booktabs。"
            "行内公式使用 $...$，独立公式使用 \\[...\\] 或 equation/align 环境。"
            "文本中的特殊字符要正确转义（例如百分号写为 \\%）。"
            "登记的数值引用仍使用 {{R:Rn}}；登记图引用 {{F:Fn}} 独占一行插图，"
            "行内使用同一引用指代；不要自行添加 figure、caption、图片 label、图片路径或 includegraphics。"
            "这些占位引用由系统原生替换为 LaTeX 数值和 figure 环境，无需格式转换。"
        )
    return STYLE + "\n只输出本节 Markdown 正文，不要输出本节标题或代码；小节标题用 ### 开头。"


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
    if not any("参考文献" in title or "reference" in title.lower() for _, title, _ in plan):
        plan.append(("references", "参考文献", 2))
    return plan


def current_premises(state: PaperState, sid: str | None = None) -> List[Dict]:
    return [a for a in state["analysis"]["assumptions"] if a.get("status", "active") == "active"
            and (sid is None or sid in a["task_ids"])]


def _design_text(design: Dict, premises: List[Dict]) -> str:
    chosen = design["chosen"]
    return (
        f"方案：{chosen['name']}\n思路：{chosen['idea']}\n数学表述：{chosen['formulation']}\n"
        f"当前采用的假设：{'；'.join(a['statement'] for a in premises) or '无新增假设'}\n检验：{chosen['validation']}"
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
        + (_design_text(design, current_premises(state, sid)) + "\n" if design else "")
        + f"求解报告：\n{_report_text(report)}\n登记结果：\n{ctx.registry.to_prompt_table(sid)}"
    )


def section_request(key: str, title: str, state: PaperState, ctx: GraphContext) -> str:
    """每一节都带上全文结论表和统一口径，各节结论口径一致"""
    if key == "references":
        return "只记录已提供且可核验的来源；缺少书目信息时明确标注缺口，不得编造作者、年份、题名或 DOI。"
    request = _section_body(key, title, state, ctx)
    from ..registry import ResultRegistry
    allowed = None
    if key.startswith("solve:"):
        sid = key.split(":", 1)[1]
        sp = next(sp for sp in state["analysis"]["subproblems"] if sp["id"] == sid)
        allowed = {sid, *(sp.get("depends_on") or [])}
    references = ResultRegistry(ctx.fs, results={rid: r for rid, r in ctx.registry.results.items()
                                               if allowed is None or r.subproblem in allowed})
    request += "\n\n# 本节可用引用（仅可复制这里的编号）\n" + references.to_prompt_table()
    if not any(r.kind == "figure" for r in references.results.values()):
        request += "\n本节没有可用图表，只能用文字、公式或基于已登记结果的表格说明，不得引用图。"
    shared_ctx = ctx
    conflicts = state.get("consistency") or []
    if ctx.research and key.startswith("solve:"):
        from dataclasses import replace
        sid = key.split(":", 1)[1]
        subproblem = next(sp for sp in state["analysis"]["subproblems"] if sp["id"] == sid)
        source_ids = {sid, *(subproblem.get("depends_on") or [])}
        own_claims = {c.id for c in ctx.registry.claims.values() if c.subproblem in source_ids}
        conflicts = [c for c in conflicts if own_claims & set(c["claims"])]
        related = own_claims | {cid for c in conflicts for cid in c["claims"]}
        claims = {cid: c for cid, c in ctx.registry.claims.items() if cid in related}
        result_ids = {rid for c in claims.values() for rid in c.evidence}
        registry = ResultRegistry(ctx.fs,
            results={rid: r for rid, r in ctx.registry.results.items() if rid in result_ids}, claims=claims)
        shared_ctx = replace(ctx, registry=registry)
    shared = consistency_text(shared_ctx, conflicts)
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
            f"篇幅控制在 1500~3000 字，次要的检验细节一句话带过。"
            f"\n\n{subproblem_brief(state, ctx, subproblem)}"
        )

    if key == "restatement":
        return f"撰写「问题重述」，包括问题背景和需要解决的问题（逐条列出）。\n\n题目：\n{state['task']}"
    if key == "analysis":
        return (
            f"撰写「问题分析」，逐个子问题说明分析思路和选用模型的理由。\n\n整体思路：{analysis['overall_analysis']}\n\n"
            + "\n\n".join(
                f"{sp['id']} {sp['title']}\n" + _design_text(state["designs"][sp["id"]], current_premises(state, sp['id']))
                for sp in subproblems if sp["id"] in (state.get("designs") or {})
            )
        )
    if key == "assumptions":
        premises = current_premises(state)
        return (
            "撰写「模型假设」，仅使用下列当前采用的假设，合并去重并解释其作用和适用范围。"
            "数量按实际需要，不得补凑、改写出新的前提或把题目给定事实当成假设。"
            "没有假设时如实说明采用题目给定条件。不要把内部编号、版本或来源字段写进正文。\n\n"
            + "\n".join(f"- {a['id']} v{a.get('revision', 1)}：{a['statement']}；适用任务：{', '.join(a['task_ids'])}"
                        for a in premises)
        )
    solved_text = "\n\n".join(clip(sections.get(solve_key(sp["id"]), ""), 4000) for sp in subproblems)
    if key == "symbols":
        table = "原生 LaTeX tabular 表格（符号、说明、单位）" if ctx.output_mode == "latex" else "Markdown 表格（符号 | 说明 | 单位）"
        return f"撰写「符号说明」，用{table}，只列下文公式里实际用到的主要符号，不超过 15 行，不列代码变量名。\n\n求解章节：\n{solved_text}"
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
        keyword = r"\textbf{关键词：}" if ctx.output_mode == "latex" else "**关键词**："
        return (
            "撰写「摘要」：一段总述 + 每个子问题一段（2~4 句：用了什么方法、得到什么结论，只引用 1~3 个最关键的登记结果）"
            "+ 一段总结，全文不超过 1000 字，不写检验统计量的罗列，不用小节标题；"
            f"最后一行写「{keyword}」并列出 4~6 个关键词。\n\n"
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
    if key == "references":
        return "来源缺口：当前研究尚未登记可核验的书目来源。题目、上传数据与本研究计算记录仅作为研究材料，不冒充公开文献。正式提交前，请补充实际使用文献的作者、题名、出版信息及可核验链接，并逐项核对其与正文论述的对应关系。"
    request = section_request(key, title, state, ctx)
    with labelled(key):
        return await call_text(ctx.writer_llm, as_messages(writing_style(ctx), request))


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
    sections: Dict[str, str] = dict(state.get("sections") or {}) if ctx.research else {}
    contexts = {}
    if ctx.research:
        from ...research.store import digest
        previous = ctx.research.snapshot()["sections"]
        for key, title in solve_items:
            contexts[key] = digest({"style": writing_style(ctx), "request": section_request(key, title, state, ctx)})
            if previous.get(key, {}).get("context_hash") != contexts[key]:
                sections.pop(key, None)
    sections.update(await write_many(ctx, state, [(k, t) for k, t in solve_items if k not in sections]))

    # 摘要只依据各问的求解报告和登记结果，不读其他章节，和其余章节一起并行写，少一轮串行等待
    await ctx.set_stage("write", "in_progress", "撰写其余章节和摘要")
    with_solved = {**state, "sections": sections}
    rest = [(key, title) for key, title, _ in plan if key != "solve_heading" and not key.startswith("solve:")]
    if ctx.research:
        for key, title in rest:
            contexts[key] = digest({"style": writing_style(ctx), "request": section_request(key, title, with_solved, ctx)})
            if previous.get(key, {}).get("context_hash") != contexts[key]:
                sections.pop(key, None)
    sections.update(await write_many(ctx, with_solved, [(k, t) for k, t in rest if k not in sections]))
    await ctx.set_stage("write", "completed", f"{len(sections)} 个章节")
    log = f"write: {len(sections)} sections" + (f" (template outline: {len(outline)})" if outline else "")
    return {"sections": sections, "section_contexts": contexts, "outline": outline, "log": [log]}
