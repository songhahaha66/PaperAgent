"""Check required output, real references and LaTeX structure, then repair writing.

Scientific conclusions are reviewed against actual evidence, never inferred from
text length, literal-number regexes or whether every registered figure is used.
"""

from ai_system.research.domain import findings as research_findings

import asyncio
import re
from typing import Dict, List

from langgraph.runtime import Runtime
from langgraph.graph import END

from ai_system.graph.context import GraphContext
from ai_system.graph.llm_utils import as_messages, call_text
from ai_system.graph.registry import REF_PATTERN, find_refs
from ai_system.research.models import IssueReport
from ai_system.graph.state import PaperState
from ai_system.graph.tracing import labelled
from ai_system.modules.writing.consistency import audit_sections
from ai_system.modules.writing.sections import writing_style, section_plan, section_request, solve_key

def check_sections(state: PaperState, ctx: GraphContext) -> List[Dict[str, str]]:
    sections = state.get("sections") or {}
    registry = ctx.registry
    issues: List[Dict[str, str]] = []
    labels = {label for body in sections.values() for label in re.findall(r"\\label\{([^{}]+)\}", body)}

    for key, title, _ in section_plan(state):
        if key == "solve_heading":
            continue
        text = sections.get(key, "")
        from ai_system.research.citations import citation_issues, literature_root
        issues.extend({'section':key,'message':message} for message in citation_issues(literature_root(ctx),text))
        if not text.strip():
            issues.append({"section": key, "message": f"必需章节「{title}」为空"})
            continue
        if ctx.output_mode == "latex":
            from ai_system.export.latex import fragment_issues
            issues.extend({"section": key, "message": message} for message in fragment_issues(text))
            unknown_labels = sorted(set(re.findall(r"\\(?:ref|eqref|pageref)\{([^{}]+)\}", text)) - labels)
            if unknown_labels:
                issues.append({"section": key, "message": "引用了未定义的 LaTeX 标签：" + ", ".join(unknown_labels) + "。图引用只能用本节登记的 {{F:Fn}}，公式引用需有对应 label。"})
        unknown = [ref for ref in find_refs(text) if registry.get(ref) is None]
        if unknown:
            issues.append({"section": key, "message": f"引用了不存在的结果：{', '.join(sorted(set(unknown)))}"})
        for ref in re.finditer(r"\{\{\s*R:\s*(R\d+)\s*\}\}\s*\[\s*\d+\s*\]", text):
            result = registry.get(ref.group(1))
            if result and result.kind == "series":
                issues.append({"section": key, "message": "数值序列引用不支持 [0] 等索引。引用代表完整序列，请整组展示，或使用已经逐项登记的真实引用；不要把同一个序列填到多个单值单元格。"})
                break
    return issues


async def validate(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    await ctx.set_stage("validate", "in_progress")
    from ai_system.modules.registry import invoke
    await invoke(ctx, 'literature.verify', texts=list((state.get('sections') or {}).values()))
    reports = [IssueReport(task_id=i["section"].split(":", 1)[1] if i["section"].startswith("solve:") else state["analysis"]["subproblems"][0]["id"],
        category="writing", section=i["section"], message=i["message"]) for i in check_sections(state, ctx)]
    if ctx.check_claims:
        flagged = {issue.section for issue in reports}
        reports += [i for i in await audit_sections(state, ctx) if i.section not in flagged]
    issues = ctx.research.report_issues(reports, "sections")
    active_service=[i for i in research_findings(ctx.research.snapshot()) if i['category']=='service' and i['status'] not in {'resolved','dismissed','superseded'}
                    and (i.get('review_scope',{}).get('stage') in {'sections','citations'} or i['source']=='sections')]
    issues=list({i['id']:i for i in [*issues,*active_service]}.values())
    research_restart = any(i['category']=='coverage' or i['category']!='service' and i["blocking"] and i["action"] != "rewrite" for i in issues)
    blocked = bool(active_service) or any(i["category"]=="service" for i in issues) or bool(issues) and not research_restart and state.get("repair_round", 0) >= ctx.max_repair_rounds
    ctx.research.save_sections(state.get("sections") or {}, issues, state.get("section_contexts"))
    from services.chat_services.issue_edits import bind_findings
    bind_findings(ctx.research,[i['id'] for i in issues],state.get('sections') or {})
    if blocked:
        await ctx.set_stage("validate", "blocked", "审查未完成或章节修复尚未解决，保留草稿，等待恢复")
    from ai_system.research.control import public_snapshot
    await ctx.events.emit("research_state", public_snapshot(ctx.research.snapshot()))
    ctx.write_json(".system/validation.json", {"round": state.get("repair_round", 0), "issues": issues})
    note = f"第 {state.get('repair_round', 0)} 轮：{len(issues)} 个问题"
    await ctx.events.emit("paper_validation", note if issues else "校验通过")
    return {"issues": issues, "research_restart": research_restart, "research_blocked": blocked,
            "log": [f"validate: {len(issues)} issues"]}


def route_after_validate(state: PaperState, runtime: Runtime[GraphContext]) -> str:
    if state.get("research_blocked"):
        return END
    if state.get("research_restart"):
        return "observe"
    if state.get("issues"):
        return "repair"
    return "render"


async def repair(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    titles = {key: title for key, title, _ in section_plan(state)}
    by_section: Dict[str, List[str]] = {}
    for issue in state.get("issues") or []:
        by_section.setdefault(issue["section"], []).append(issue["message"])
    await ctx.set_stage("validate", "in_progress", f"修复 {len(by_section)} 个章节")

    async def fix(key: str, problems: List[str]) -> str:
        current = (state.get("sections") or {}).get(key, "")
        request = section_request(key, titles[key], state, ctx)
        prompt = (
            f"{request}\n\n# 当前稿\n{current or '（空）'}\n\n# 校验发现的问题（必须全部修正）\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\n\n请输出修正后的完整本节正文。"
        )
        with labelled(f"repair:{key}"):
            return await call_text(ctx.writer_llm, as_messages(writing_style(ctx), prompt))

    keys = list(by_section)
    texts = await asyncio.gather(*(fix(k, by_section[k]) for k in keys))
    return {
        "sections": dict(zip(keys, texts)),
        "repair_round": state.get("repair_round", 0) + 1,
        "log": [f"repair: {', '.join(keys)}"],
    }


HEADING_PATTERN = re.compile(r"^(#{1,6})(\s+\S.*)$", re.MULTILINE)


def demote_headings(text: str, level: int) -> str:
    """章节正文里的小标题统一放到本章标题之下一级，避免与章节标题同级"""
    levels = [len(m.group(1)) for m in HEADING_PATTERN.finditer(text)]
    if not levels:
        return text
    shift = level + 1 - min(levels)
    if shift <= 0:
        return text
    return HEADING_PATTERN.sub(lambda m: "#" * min(len(m.group(1)) + shift, 6) + m.group(2), text)


FIGURE_LINE = re.compile(r"^[ \t]*\{\{\s*F:\s*(F\d+)\s*\}\}[ \t]*$", re.MULTILINE)
INLINE_FIGURE = re.compile(r"\{\{\s*F:\s*(F\d+)\s*\}\}")


def place_figures(text: str) -> str:
    """只在行内提到（"见 {{F:F1}}"）、没有单独一行插入的图，插到首次提到它的段落之后

    行内引用只渲染成"图 k"，2024_C 的论文四处写"见图1"，图却一张都没出现。
    """
    placed = set(FIGURE_LINE.findall(text))
    blocks: Dict[int, str] = {}
    for match in INLINE_FIGURE.finditer(text):
        fig = match.group(1)
        if fig in placed:
            continue
        placed.add(fig)
        end = text.find("\n\n", match.end())
        end = len(text) if end < 0 else end
        blocks[end] = blocks.get(end, "") + f"\n\n{{{{F:{fig}}}}}"
    # 从后往前插，前面的位置不受影响
    for end in sorted(blocks, reverse=True):
        text = text[:end] + blocks[end] + text[end:]
    return text


def assemble(state: PaperState) -> str:
    sections = state.get("sections") or {}
    solve_keys = {solve_key(sp["id"]) for sp in state["analysis"]["subproblems"]}
    lines = [f"# {state['analysis']['title']}", ""]
    for key, title, level in section_plan(state):
        lines.append(f"{'#' * level} {title}")
        lines.append("")
        if key != "solve_heading":
            text = demote_headings(sections.get(key, "").strip(), level)
            # 图只放在求解章节里；摘要、灵敏度分析等处独占一行的图改成行内引用
            lines.append(place_figures(text) if key in solve_keys else FIGURE_LINE.sub(r"（见{{F:\1}}）", text))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def assemble_latex(state: PaperState) -> str:
    from ai_system.export.latex import escape_plain, native_document
    sections = state.get("sections") or {}
    body = []
    solve_keys = {solve_key(sp["id"]) for sp in state["analysis"]["subproblems"]}
    for key, title, level in section_plan(state):
        if key == "abstract":
            continue
        title = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", title)
        command = "section" if level == 2 else "subsection"
        body.append("\\" + command + "{" + escape_plain(title) + "}\n")
        if key != "solve_heading":
            text = sections.get(key, "").strip()
            body.append(place_figures(text) if key in solve_keys else FIGURE_LINE.sub(r"（见{{F:\1}}）", text))
    abstract = FIGURE_LINE.sub(r"（见{{F:\1}}）", sections.get("abstract", ""))
    return native_document(state["analysis"]["title"], abstract, "\n\n".join(body))


async def render(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    if ctx.research:
        snapshot = ctx.research.refresh_inputs()
        if not ctx.research.delivery_ready(snapshot):
            if any(i['category']=='service' and i['blocking'] and i['status'] not in {'resolved','dismissed','superseded'}
                   for i in research_findings(snapshot)):
                await ctx.set_stage('render', 'blocked', '审查服务尚未完成，保留已完成的交付步骤，恢复后重试。')
                return {'research_blocked': True, 'log': ['render: pending service review; delivery position retained']}
            return {"research_restart": True, "log": ["render: research evidence changed, returning to research control"]}
        ctx.research.save_sections(state.get("sections") or {}, state.get("issues") or [], state.get("section_contexts"))
    native = ctx.output_mode == "latex"
    filename = "paper.tex" if native else "paper.md"
    if native and ctx.research:
        from services.chat_services.paper_project import sync_generated
        from services.chat_services.paper_sources import write_bibliography
        sync_generated(ctx.research,state,ctx)
        write_bibliography(ctx.workspace)
        # Read the actual project source, preserving its preamble and user edits.
        raw = (ctx.workspace / 'paper.tex').read_text(encoding='utf-8')
    else:
        raw = assemble_latex(state) if native else assemble(state)
    # 带引用的原稿留在 .system，方便追溯每个数字的来源
    ctx.fs.write_text(".system/paper_raw" + (".tex" if native else ".md"), raw)
    from ai_system.graph.registry import publication_text
    from ai_system.research.citations import render_citations, literature_root,citation_issues
    if native and ctx.research:
        from services.chat_services.paper_sources import validate_references
        validate_references(ctx.workspace,raw,raw)
        problems=[]
    else:
        problems=citation_issues(literature_root(ctx),raw)
    if problems:raise ValueError('发布前文献引用未获原文支持：'+'；'.join(problems))
    rendered = raw if native and ctx.research else publication_text(render_citations(literature_root(ctx),ctx.registry.render(raw, latex=native),native))
    if REF_PATTERN.search(rendered):
        raise ValueError("论文仍有未解析的登记引用，禁止发布")
    if native and ctx.research:
        path, conflict = filename, False
    else:
        path, conflict = ctx.research.publish_paper(rendered, filename=filename) if ctx.research else (filename, False)
    if ctx.research:
        ctx.fs.register_file(path, kind="document", created_by="ai", visibility="user")
    else:
        ctx.fs.write_text(path, rendered, kind="document", created_by="ai", visibility="user")
    remaining = len(state.get("issues") or [])
    await ctx.set_stage("validate", "completed", f"剩余 {remaining} 个问题" if remaining else "全部通过")
    await ctx.set_stage("render", "completed", f"登记结果 {len(ctx.registry.results)} 项")
    await ctx.events.file_changed(path)
    return {"paper_path": path, "artifact_conflict": conflict, "research_restart": False, "log": [f"render: {path}"]}
