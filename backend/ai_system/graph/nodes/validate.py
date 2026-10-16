"""
阶段 6：确定性校验 + 定向修复，阶段 7：渲染

校验全部由代码完成（不调模型）：
- 章节缺失或过短；
- 引用了不存在的结果编号；
- 正文出现没有引用登记结果的小数/百分数（题目中出现过的数不算；公式里写成 = 4.05 的也算）；
- 子问题有登记结果，但求解章节一个都没引用。
以上不调模型。另外用一次模型调用检查汇总性章节与结论表是否一致（consistency.audit_sections），
问题合并进同一个清单。有问题的章节交给写作模型带着问题清单重写，最多 max_repair_rounds 轮，其余章节不动。
"""

import asyncio
import re
from typing import Dict, List

from langgraph.runtime import Runtime

from ..context import GraphContext
from ..llm_utils import as_messages, call_text
from ..registry import MATH_PATTERN, REF_PATTERN, find_refs
from ...research.models import IssueReport
from ..state import PaperState
from ..tracing import labelled
from .consistency import audit_sections
from .review import CONVENTIONS
from .write import STYLE, section_plan, section_request, solve_key

MIN_SECTION_CHARS = 80
# 小数或百分数；整数（年份、编号、个数）不检查
DECIMAL_PATTERN = re.compile(r"(?<![\w.])-?\d+\.\d+%?|(?<![\w.])\d+%")
STRIP_PATTERNS = [
    re.compile(r"\$\$.*?\$\$", re.DOTALL),
    re.compile(r"\$[^$\n]+\$"),
    re.compile(r"```.*?```", re.DOTALL),
    re.compile(r"`[^`\n]+`"),
    REF_PATTERN,
    # 小标题编号（"### 4.1 整体思路"）不是结果；v2m 的问题分析因此被报了 5 个"未引用的数字"
    re.compile(r"^[ \t]*#{1,6}[ \t]*[A-Za-z]*\d+(?:\.\d+)+", re.MULTILINE),
]


# 公式里的小数一般是常数或系数，不查；但"= 4.05"这样单独等于一个数的是结果，要查。
# 2024_C 的 Q2 把没登记的检验统计量写成 $z = 4.05$，原来整段公式都被跳过
MATH_RESULT = re.compile(r"(?:=|≈|\\approx)\s*(-?\d+\.\d+%?)(?![\w.(\\])")


def _prose(text: str) -> str:
    assigned = [m.group(1) for span in MATH_PATTERN.findall(REF_PATTERN.sub(" ", text)) for m in MATH_RESULT.finditer(span)]
    for pattern in STRIP_PATTERNS:
        text = pattern.sub(" ", text)
    return text + " " + " ".join(assigned)


def _normalize(number: str) -> str:
    return number.rstrip("%").lstrip("-").rstrip("0").rstrip(".") or "0"


def untracked_numbers(text: str, allowed_text: str) -> List[str]:
    allowed = {_normalize(m.group()) for m in DECIMAL_PATTERN.finditer(allowed_text)}
    found = []
    for match in DECIMAL_PATTERN.finditer(_prose(text)):
        value = match.group()
        if _normalize(value) not in allowed and value not in found and value not in CONVENTIONS:
            found.append(value)
    return found


def check_sections(state: PaperState, ctx: GraphContext) -> List[Dict[str, str]]:
    sections = state.get("sections") or {}
    registry = ctx.registry
    issues: List[Dict[str, str]] = []
    # 题目原文中的数字可以直接写
    allowed_text = state["task"] + "\n" + "\n".join(r.value for r in registry.results.values())

    for key, title, _ in section_plan(state):
        if key == "solve_heading":
            continue
        text = sections.get(key, "")
        if len(text.strip()) < MIN_SECTION_CHARS:
            issues.append({"section": key, "message": f"「{title}」缺失或过短"})
            continue
        unknown = [ref for ref in find_refs(text) if registry.get(ref) is None]
        if unknown:
            issues.append({"section": key, "message": f"引用了不存在的结果：{', '.join(sorted(set(unknown)))}"})
        loose = untracked_numbers(text, allowed_text)
        if loose:
            issues.append({
                "section": key,
                "message": f"出现未引用登记结果的数字：{', '.join(loose[:8])}。请改为对应的 {{{{R:Rn}}}} 引用，没有对应结果的数字删掉或改为定性描述",
            })

    for sp in state["analysis"]["subproblems"]:
        key = solve_key(sp["id"])
        recorded = registry.for_subproblem(sp["id"])
        text = sections.get(key, "")
        if recorded and text and not set(find_refs(text)) & {r.id for r in recorded}:
            issues.append({"section": key, "message": f"{sp['id']} 有登记结果，但本节没有引用任何一个"})
        figures = [r.id for r in recorded if r.kind == "figure"]
        if figures and text and not set(find_refs(text)) & set(figures):
            issues.append({"section": key, "message": f"{sp['id']} 登记了图 {', '.join(figures)}，本节没有插入"})
    return issues


async def validate(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    await ctx.set_stage("validate", "in_progress")
    reports = [IssueReport(task_id=i["section"].split(":", 1)[1] if i["section"].startswith("solve:") else state["analysis"]["subproblems"][0]["id"],
        category="writing", section=i["section"], message=i["message"]) for i in check_sections(state, ctx)]
    if ctx.check_claims:
        flagged = {issue.section for issue in reports}
        reports += [i for i in await audit_sections(state, ctx) if i.section not in flagged]
    issues = ctx.research.report_issues(reports, "sections")
    research_restart = any(i["blocking"] for i in issues)
    ctx.write_json(".system/validation.json", {"round": state.get("repair_round", 0), "issues": issues})
    note = f"第 {state.get('repair_round', 0)} 轮：{len(issues)} 个问题"
    await ctx.events.emit("paper_validation", note if issues else "校验通过")
    return {"issues": issues, "research_restart": research_restart, "log": [f"validate: {len(issues)} issues"]}


def route_after_validate(state: PaperState, runtime: Runtime[GraphContext]) -> str:
    if state.get("research_restart"):
        return "observe"
    if state.get("issues") and state.get("repair_round", 0) < runtime.context.max_repair_rounds:
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
            return await call_text(ctx.writer_llm, as_messages(STYLE, prompt))

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


async def render(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    if ctx.research:
        snapshot = ctx.research.refresh_inputs()
        if not ctx.research.delivery_ready(snapshot):
            return {"research_restart": True, "log": ["render: inputs changed, returning to research control"]}
        ctx.research.save_sections(state.get("sections") or {}, state.get("issues") or [], state.get("section_contexts"))
    raw = assemble(state)
    # 带引用的原稿留在 .system，方便追溯每个数字的来源
    ctx.fs.write_text(".system/paper_raw.md", raw)
    rendered = ctx.registry.render(raw)
    path, conflict = ctx.research.publish_paper(rendered) if ctx.research else ("paper.md", False)
    if ctx.research:
        ctx.fs.register_file(path, kind="document", created_by="ai", visibility="user")
    else:
        ctx.fs.write_text(path, rendered, kind="document", created_by="ai", visibility="user")
    remaining = len(state.get("issues") or [])
    await ctx.set_stage("validate", "completed", f"剩余 {remaining} 个问题" if remaining else "全部通过")
    await ctx.set_stage("render", "completed", f"登记结果 {len(ctx.registry.results)} 项")
    await ctx.events.file_changed(path)
    return {"paper_path": path, "artifact_conflict": conflict, "research_restart": False, "log": [f"render: {path}"]}
