"""Read-only document/editor helpers; research sessions own all drafting."""
import re
from typing import Dict, List, Tuple


from ai_system.graph.context import GraphContext
from ai_system.graph.state import PaperState

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
