"""Check manuscript files and references; return diagnostics to the research session.

Scientific conclusions are reviewed against actual evidence, never inferred from
text length, literal-number regexes or whether every registered figure is used.
"""
import re
from typing import Dict, List

from ai_system.graph.context import GraphContext
from ai_system.graph.registry import REF_PATTERN, find_refs
from ai_system.graph.state import PaperState
from ai_system.modules.writing.sections import section_plan, solve_key

def check_sections(state: PaperState, ctx: GraphContext) -> List[Dict[str, str]]:
    sections = state.get("sections") or {}
    registry = ctx.registry
    if ctx.output_mode == 'latex':
        # Syntax, cross-references and layout are checked by the compiler.
        from ai_system.research.citations import citation_issues
        return [{'section':key, 'message':message} for key,text in sections.items()
                for message in [*citation_issues(ctx.research.root,text), *(['正文仍有未解析的结果引用，请基于登记证据修复源码'] if REF_PATTERN.search(text) else [])]]
    issues: List[Dict[str, str]] = []

    for key, title, _ in ([(k,k,2) for k in sections] if state.get('document') else section_plan(state)):
        if key == "solve_heading":
            continue
        text = sections.get(key, "")
        from ai_system.research.citations import citation_issues, literature_root
        issues.extend({'section':key,'message':message} for message in citation_issues(literature_root(ctx),text))
        if not text.strip():
            issues.append({"section": key, "message": f"必需章节「{title}」为空"})
            continue
        unknown = [ref for ref in find_refs(text) if registry.get(ref) is None]
        if unknown:
            issues.append({"section": key, "message": f"引用了不存在的结果：{', '.join(sorted(set(unknown)))}"})
        if message:=registry.series_index_issue(text):issues.append({'section':key,'message':message})
        # Validate actual local image paths, without registering figures.
        from urllib.parse import urlsplit, unquote
        for image in re.findall(r'!\[[^\]]*\]\(([^)]+)\)',text):
            path=image.strip().split(' "',1)[0].strip('<>')
            if urlsplit(path).scheme:continue
            try:
                if not ctx.fs.abs(unquote(path)).is_file():
                    raise ValueError('文件不存在')
            except (ValueError,OSError):
                issues.append({'section':key,'message':'图片文件不可用：'+path})

    return issues

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
