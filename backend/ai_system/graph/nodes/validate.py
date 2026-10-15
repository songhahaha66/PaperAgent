"""
阶段 6：确定性校验 + 定向修复，阶段 7：渲染

校验全部由代码完成（不调模型）：
- 章节缺失或过短；
- 引用了不存在的结果编号；
- 正文出现没有引用登记结果的小数/百分数（题目中出现过的数不算）；
- 子问题有登记结果，但求解章节一个都没引用。
有问题的章节交给写作模型带着问题清单重写，最多 max_repair_rounds 轮，其余章节不动。
"""

import asyncio
import re
from typing import Dict, List

from langgraph.runtime import Runtime

from ..context import GraphContext
from ..llm_utils import as_messages, call_text
from ..registry import REF_PATTERN, find_refs
from ..state import PaperState
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
]


def _prose(text: str) -> str:
    for pattern in STRIP_PATTERNS:
        text = pattern.sub(" ", text)
    return text


def _normalize(number: str) -> str:
    return number.rstrip("%").lstrip("-").rstrip("0").rstrip(".") or "0"


def untracked_numbers(text: str, allowed_text: str) -> List[str]:
    allowed = {_normalize(m.group()) for m in DECIMAL_PATTERN.finditer(allowed_text)}
    found = []
    for match in DECIMAL_PATTERN.finditer(_prose(text)):
        value = match.group()
        if _normalize(value) not in allowed and value not in found:
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
    issues = check_sections(state, ctx)
    ctx.write_json(".system/validation.json", {"round": state.get("repair_round", 0), "issues": issues})
    note = f"第 {state.get('repair_round', 0)} 轮：{len(issues)} 个问题"
    await ctx.events.emit("paper_validation", note if issues else "校验通过")
    return {"issues": issues, "log": [f"validate: {len(issues)} issues"]}


def route_after_validate(state: PaperState, runtime: Runtime[GraphContext]) -> str:
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
        return await call_text(ctx.writer_llm, as_messages(STYLE, prompt))

    keys = list(by_section)
    texts = await asyncio.gather(*(fix(k, by_section[k]) for k in keys))
    return {
        "sections": dict(zip(keys, texts)),
        "repair_round": state.get("repair_round", 0) + 1,
        "log": [f"repair: {', '.join(keys)}"],
    }


def assemble(state: PaperState) -> str:
    sections = state.get("sections") or {}
    lines = [f"# {state['analysis']['title']}", ""]
    for key, title, level in section_plan(state):
        lines.append(f"{'#' * level} {title}")
        lines.append("")
        if key != "solve_heading":
            lines.append(sections.get(key, "").strip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def render(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    raw = assemble(state)
    # 带引用的原稿留在 .system，方便追溯每个数字的来源
    ctx.fs.write_text(".system/paper_raw.md", raw)
    ctx.fs.write_text("paper.md", ctx.registry.render(raw), kind="document", created_by="ai", visibility="user")
    remaining = len(state.get("issues") or [])
    await ctx.set_stage("validate", "completed", f"剩余 {remaining} 个问题" if remaining else "全部通过")
    await ctx.set_stage("render", "completed", f"登记结果 {len(ctx.registry.results)} 项")
    await ctx.events.file_changed("paper.md")
    return {"paper_path": "paper.md", "log": ["render: paper.md"]}
