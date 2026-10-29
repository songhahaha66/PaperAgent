"""
阶段 5：按执行记录与结果文件写作

先并行写每个子问题的"模型建立与求解"，再并行写重述、分析、假设、符号、灵敏度、评价和摘要。
用户上传了模板时，章节标题和顺序按模板（outline.py），模板里对不上的章节按模板标题单独撰写。
写作读取实际执行输出、文件清单和报告，直接使用实际数值与图片路径。
"""

import asyncio
import json
import re
from typing import Dict, List, Tuple

from langgraph.runtime import Runtime

from ai_system.graph.context import GraphContext
from ai_system.graph.outline import template_outline
from ai_system.graph.llm_utils import as_messages, call_text, clip, cap_output
from ai_system.graph.state import PaperState
from ai_system.graph.tracing import labelled

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
    "你是数学建模论文的执笔手，使用严谨、简洁的中文。"
    "依据实际执行记录、结果文件和研究报告写作，直接写数值、单位及适用条件，不创建R/F结果编号。"
    "计算执行成功不代表模型正确；区分原题事实、建模假设、已验证结果和未知。"
    "实测差异不能代替统计显著性，公式须匹配实际算法、预处理和边界条件。"
    "只插入已提供的实际图片文件，使用Markdown图片路径；没有生成的图不得声称存在。"
    "关键结果用表格组织，保留数据来源、校验与局限；完整精度和序列留在结果文件。"
)


def writing_style(ctx: GraphContext) -> str:
    compact=('\n本研究选用简短论文：求解小节约600–900字，其他每节约100–300字，保留证据、来源与局限，不重复扩展实验。' if ctx.compact_paper else '')
    return STYLE + compact + "\n只输出本节 Markdown 正文，不要输出本节标题或代码；小节标题用 ### 开头。"


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
            for question in state['analysis']['questions']:
                if not question['experiment_ids']:continue
                anchor=question['experiment_ids'][0]
                if not any(k==solve_key(anchor) for k,_ in top):
                    plan.append((solve_key(anchor), question['title'], 3))
    if not any("参考文献" in title or "reference" in title.lower() for _, title, _ in plan):
        plan.append(("references", "参考文献", 2))
    return plan


COMPACT_OUTLINE = [
    {"key":"abstract","title":"摘要"},
    {"key":"solve_heading","title":"问题、研究设计与实测结果"},
    {"key":"evaluation","title":"检验、局限与结论"},
]


def current_premises(state: PaperState, sid: str | None = None) -> List[Dict]:
    return [a for a in state["analysis"]["assumptions"] if a.get("status", "active") == "active"
            and (sid is None or sid in a["task_ids"])]


def subproblem_brief(ctx: GraphContext, subproblem: Dict) -> str:
    from ai_system.research.views import experiment_context
    record=experiment_context(ctx.research.snapshot(),{subproblem['id']})[0]
    return json.dumps({k:v for k,v in record.items() if k!='results'},ensure_ascii=False)


def _comparison_context(ctx: GraphContext, allowed) -> str:
    from ai_system.modules.experiments.store import ExperimentStore
    rows=[row for row in ExperimentStore(ctx.research).accepted_summaries(ctx.research.snapshot())
          if allowed is None or row['task_id'] in allowed]
    return ('\n# 共同数据划分下的实测比较（仅适用于所列条件，不证明统计显著）\n'
            +json.dumps(rows,ensure_ascii=False)) if rows else ''


def section_request(key: str, title: str, state: PaperState, ctx: GraphContext) -> str:
    """每一节都带上全文结论表和统一口径，各节结论口径一致"""
    if key == "references":
        return reference_section(state, ctx)
    request = _section_body(key, title, state, ctx)
    planned=next((s for s in state.get('outline',[]) if s['key']==key),{})
    if planned.get('purpose'):
        request+='\n\n# 本节研究表达目的与已核对依据\n'+json.dumps(planned,ensure_ascii=False)
        # Persisted outlines used to accept free-form descriptive basis entries.
        # Only explicit citation refs select literature; the actual result table
        # below remains the source of computational evidence for every section.
        citations=' '.join('{{C:'+b['ref']+'}}' for b in planned.get('basis',[]) if re.fullmatch(r'C[1-9]\d*',b.get('ref','')))
        if citations:request+='\n本节已核对的文献引用：'+citations
    from ai_system.research.method_facts import method_context
    request+=method_context(getattr(ctx,'research',None))
    allowed = None
    if planned.get('task_ids'):allowed=set(planned['task_ids'])
    if key.startswith("solve:"):
        sid = key.split(":", 1)[1]
        sp = next(sp for sp in state["analysis"]["subproblems"] if sp["id"] == sid)
        question=next(q for q in state['analysis']['questions'] if sid in q['experiment_ids'])
        allowed=set(question['experiment_ids']) | {dep for item in state['analysis']['subproblems']
            if item['id'] in question['experiment_ids'] for dep in item.get('depends_on') or []}
    request += _comparison_context(ctx, allowed)
    from ai_system.research.views import execution_context
    request += '\n\n# 实际执行记录、结果文件与报告\n'+json.dumps(execution_context(ctx.research,allowed),ensure_ascii=False)
    if ctx.template_requirements:
        request += (
            "\n\n# 用户模板的要求（章节标题和顺序已按模板安排，本节只写正文；措辞、格式、篇幅以模板为准）\n"
            f"{ctx.template_requirements}"
        )
    from ai_system.research.citations import literature_root
    from ai_system.research.literature import literature_context
    if not ctx.compact_paper or key.startswith('solve:'):
        request += literature_context(literature_root(ctx), title+'\n'+request+'\n'+state.get('task','')[:500])
    else:
        request += '\n本节不重复文献背景或C引用；需要的公开资料背景由求解小节集中说明。'
    return request


def _section_body(key: str, title: str, state: PaperState, ctx: GraphContext) -> str:
    planned=next((s for s in state.get('outline',[]) if s['key']==key),{})
    if planned.get('task_ids') and not key.startswith('solve:'):
        state={**state,'analysis':{**state['analysis'],'subproblems':[
            sp for sp in state['analysis']['subproblems'] if sp['id'] in planned['task_ids']]}}
    analysis = state["analysis"]
    subproblems = analysis["subproblems"]
    sections = state.get("sections") or {}

    if key.startswith("solve:"):
        sid = key.split(":", 1)[1]
        subproblem = next(sp for sp in subproblems if sp["id"] == sid)
        question=next(q for q in analysis['questions'] if sid in q['experiment_ids'])
        experiments=[sp for sp in subproblems if sp['id'] in question['experiment_ids']]
        return (
            '围绕同一研究问题综合描述下面各实验的方法、条件、结果及判断。不要把每个方法写成不同研究问题；'
            '条件不同的结果说明边界，不能硬排名；无赢家时保留多个候选。\n'
            +json.dumps({k:question[k] for k in ('question','judgment','limitations')},ensure_ascii=False)+'\n'
            +f"撰写「{title}」的建模与求解小节，一般依次包含：模型的建立（变量、公式推导）、求解过程与算法、"
            f"结果与图表、结果检验。如果这一问的交付物是备忘录、信件、建议书等文体，"
            f"就按交付物要求的文体和篇幅直接写成正文，不要套用上面的结构。"
            + ("篇幅控制在600~900字，" if ctx.compact_paper else "篇幅控制在1500~3000字，")
            + "次要的检验细节一句话带过。"
            + ("合并交代问题与数据、采用前提及无新增假设的边界、符号定义、模型公式、共同划分、"
               "方法参数、实测结果与图、已完成检查及恢复边界。只在此节集中引用一句有公开原文支持的背景，"
               "不得把文献当成本研究结果或添加实验。" if ctx.compact_paper else "")
            + "\n\n"+"\n\n".join(subproblem_brief(ctx,item) for item in experiments)
        )

    if key == "restatement":
        if ctx.compact_paper:
            return ("合并撰写问题、数据、研究设计与假设边界，约150–250字。说明实际特征、共同固定划分、原定方法与参数，"
                    "只列已采用前提；没有新增假设时如实说明。符号、模型公式和结果详列在求解小节。本节不重复文献背景或C引用。\n\n"
                    +state['task']+'\n\n'+analysis['overall_analysis'])
        return f"撰写「问题重述」，包括问题背景和需要解决的问题（逐条列出）。\n\n题目：\n{state['task']}"
    if key == "analysis":
        return (
            f"撰写「问题分析」，逐个子问题说明分析思路和选用模型的理由。\n\n整体思路：{analysis['overall_analysis']}\n\n"
            + "\n\n".join(
                subproblem_brief(ctx,sp) for sp in subproblems
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
        table = "Markdown 表格（符号 | 说明 | 单位）"
        return f"撰写「符号说明」，用{table}，只列下文公式里实际用到的主要符号，不超过 15 行，不列代码变量名。\n\n求解章节：\n{solved_text}"
    if key == "sensitivity":
        return (
            "撰写「灵敏度分析」，汇总各子问题的检验和灵敏度分析结论，依据实际结果与文件写作。\n\n"
            + "\n\n".join(subproblem_brief(ctx, sp) for sp in subproblems)
        )
    if key == "evaluation":
        return (
            "撰写「模型的评价与推广」，分优点、缺点、改进与推广三部分，结合各子问题的局限。"
            "推广部分写清模型在实际中怎么用：谁在什么场景下用哪个输出做什么决定，哪些输出因证据不足不能用于决策。\n\n"
            + "\n\n".join(subproblem_brief(ctx, sp) for sp in subproblems)
        )
    if key == "abstract":
        keyword = "**关键词**："
        return (
            "撰写「摘要」：一段总述 + 每个子问题一段（2~4 句：用了什么方法、得到什么结论，只引用 1~3 个最关键的实际结果）"
            "+ 一段总结，全文不超过 1000 字，不写检验统计量的罗列，不用小节标题；"
            f"最后一行写「{keyword}」并列出 4~6 个关键词。\n\n"
            f"论文标题：{analysis['title']}\n\n" + "\n\n".join(subproblem_brief(ctx, sp) for sp in subproblems)
        )
    # Planner/template sections are identified by their purpose and title;
    # their storage key need not belong to the built-in outline vocabulary.
    guard = "只列出题目和数据来源，不要编造文献。" if any(w in title for w in ("参考文献", "文献", "引用")) else ""
    return (
        f"撰写「{title}」一节，按本节研究表达目的组织已取得的结果、来源和局限，依据实际结果与文件写作。{guard}\n\n"
        f"题目：\n{clip(state['task'], 3000)}\n\n"
        + "\n\n".join(subproblem_brief(ctx, sp) for sp in subproblems)
    )


def reference_section(state: PaperState, ctx: GraphContext) -> str:
    """Quote supplied bibliography only; never complete metadata from model memory."""
    text = state.get("task", "")
    entries = []
    in_references = False
    for line in text.splitlines():
        if re.match(r"^\s*#*\s*(?:参考文献|参考资料|References|Bibliography)\s*[:：]?\s*$", line, re.I):
            in_references = True
            continue
        if in_references and re.match(r"^#{1,6}\s+", line):
            in_references = False
        if line.strip() and (in_references or re.search(r"https?://|doi\s*:", line, re.I)):
            entries.append(line.strip())
    # Dedicated bibliography attachments are source material, not generated claims.
    folder = ctx.workspace / "attachment"
    if folder.exists():
        for path in sorted(folder.iterdir()):
            if path.is_file() and not path.is_symlink() and (path.suffix.lower() == ".bib" or
                    (path.suffix.lower() in {".txt", ".md"} and any(word in path.stem.lower() for word in ("reference", "bibliograph", "参考文献")))):
                entries.append("附件 " + path.name + "：\n" + path.read_text(encoding="utf-8", errors="replace")[:12000])
    entries = list(dict.fromkeys(entries))[:30]
    from ai_system.research.citations import bibliography, literature_root
    registered = bibliography(literature_root(ctx), ctx.output_mode=='latex')
    if not entries:
        if registered:
            return registered
        return "来源缺口：当前研究尚未登记可核验的书目来源。题目、上传数据与本研究计算记录仅作为研究材料，不冒充公开文献。正式提交前，请补充实际使用文献的作者、题名、出版信息及可核验链接，并逐项核对其与正文论述的对应关系。"
    note = "以下来源由用户在题目或书目附件中提供，书目信息按原文保留，尚未独立核验。待完善：核对作者、题名、年份、出版信息和正文对应关系；原文缺少的字段仍为缺口，系统未补造。"
    if ctx.output_mode == "latex":
        from ai_system.export.latex import escape_plain
        def reference_text(item):
            # Plain escaped URLs form unbreakable lines and can leave the page.
            # hyperref already loads url; its verbatim argument keeps query
            # characters intact while permitting line breaks within the link.
            parts = re.split(r"(https?://[^\s{}\\]+)", item)
            return ''.join(r'\url{' + part + '}' if index % 2 else escape_plain(part)
                           for index, part in enumerate(parts))
        return note + "\n\\begin{itemize}\n" + "\n".join("\\item " + reference_text(item) for item in entries) + "\n\\end{itemize}" + ('\n\n'+registered if registered else '')
    return note + "\n\n" + "\n\n".join("- " + item for item in entries) + ('\n\n'+registered if registered else '')


async def write_section(ctx: GraphContext, state: PaperState, key: str, title: str) -> str:
    if key == "references":
        return reference_section(state, ctx)
    request = section_request(key, title, state, ctx)
    with labelled(key):
        return await call_text(cap_output(ctx.writer_llm, 4800) if ctx.compact_paper else ctx.writer_llm,
                               as_messages(writing_style(ctx), request))


async def write_many(ctx: GraphContext, state: PaperState, items: List[Tuple[str, str]]) -> Dict[str, str]:
    async def one(key,title):
        text=await write_section(ctx,state,key,title)
        from ai_system.research.store import digest
        context=digest({'style':writing_style(ctx),'request':section_request(key,title,state,ctx)})
        ctx.research.save_sections({key:text},['章节写作尚未完成，未发布论文'],{key:context}, verified=False)
        return text
    texts = await asyncio.gather(*(one(key,title) for key,title in items),return_exceptions=True)
    errors=[text for text in texts if isinstance(text,BaseException)]
    if errors:raise errors[0]
    return dict(zip([key for key, _ in items], texts))


async def write(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    await ctx.set_stage("solve", "completed")
    await ctx.set_stage("write", "in_progress", "撰写求解章节")
    await ctx.events.stage("开始撰写论文")

    outline = state.get('outline') or []
    if ctx.template_requirements:
        supplied=template_outline(ctx.template_requirements)
        if supplied:
            chosen={s['key']:s for s in outline};combined=[]
            for item in supplied:
                combined.append({**chosen.get(item['key'],{}),**item})
                if item['key']=='solve_heading':combined.extend(s for s in outline if s['key'].startswith('solve:'))
            outline=combined
    if not outline and ctx.compact_paper:
        outline = COMPACT_OUTLINE
    state = {**state, "outline": outline}
    plan = section_plan(state)
    solve_items = [(key, title) for key, title, _ in plan if key.startswith("solve:")]
    sections: Dict[str, str] = {}
    contexts = {}
    from ai_system.research.store import digest
    previous = ctx.research.snapshot()["sections"]
    for key, title in solve_items:
        contexts[key] = digest({"style": writing_style(ctx), "request": section_request(key, title, state, ctx)})
        if previous.get(key, {}).get("context_hash") == contexts[key] and previous[key]['validity']=='current':
            sections[key]=previous[key]['text']
    sections.update(await write_many(ctx, state, [(k, t) for k, t in solve_items if k not in sections]))

    # 摘要只依据各问的求解报告和结果文件，不读其他章节，和其余章节一起并行写，少一轮串行等待
    await ctx.set_stage("write", "in_progress", "撰写其余章节和摘要")
    with_solved = {**state, "sections": sections}
    rest = [(key, title) for key, title, _ in plan if key != "solve_heading" and not key.startswith("solve:")]
    for key, title in rest:
        contexts[key] = digest({"style": writing_style(ctx), "request": section_request(key, title, with_solved, ctx)})
        if previous.get(key, {}).get("context_hash") == contexts[key] and previous[key]['validity']=='current':
            sections[key]=previous[key]['text']
    sections.update(await write_many(ctx, with_solved, [(k, t) for k, t in rest if k not in sections]))
    await ctx.set_stage("write", "completed", f"{len(sections)} 个章节")
    log = f"write: {len(sections)} sections" + (f" (template outline: {len(outline)})" if outline else "")
    return {"sections": sections, "section_contexts": contexts, "outline": outline, "log": [log]}
