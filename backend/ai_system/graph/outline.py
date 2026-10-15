"""
用户模板 → 论文章节结构

上传模板时 services/file_services/template_contract.py 会在工作空间写一份模板契约（.system/template_contract.md），
其中列出模板的标题骨架：Markdown 模板是 "- L2 第3行: 摘要"，Word 模板是 "- 第5段 [Heading 1]: 一、问题重述"。
这里取出最上层的章节，按标题关键词对应到流水线的章节内容（摘要、问题重述……），对不上的记为模板自定义章节，
写作时按模板标题单独撰写。章节顺序和标题都用模板的。

规则是确定性的（不调模型），同一个模板每次得到同样的结构。
"""

import re
from typing import Dict, List, Optional, Tuple

MD_HEADING = re.compile(r"^- L(\d) 第\d+行: (.+?)\s*$", re.MULTILINE)
DOCX_HEADING = re.compile(r"^- 第\d+段 \[(?:Heading|heading|标题)\s*(\d)\]: (.+?)\s*$", re.MULTILINE)

# 按顺序匹配，先匹配到的优先（"灵敏度分析"要在"分析"之前）
KEYWORDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("abstract", ("摘要", "abstract", "summary")),
    ("sensitivity", ("灵敏度", "敏感性", "稳健性", "sensitivity", "robustness")),
    ("evaluation", ("评价", "推广", "优缺点", "优点", "evaluation", "strength", "weakness")),
    ("symbols", ("符号", "notation", "symbol")),
    ("assumptions", ("假设", "assumption")),
    ("restatement", ("重述", "背景", "引言", "restatement", "introduction", "background")),
    ("solve_heading", ("建立与求解", "模型建立", "模型的建立", "求解", "建模", "model")),
    ("analysis", ("问题分析", "分析", "analysis")),
]
SECTION_KEYS = {key for key, _ in KEYWORDS}
DEFAULT_SOLVE_TITLE = "模型的建立与求解"


def custom_key(index: int) -> str:
    return f"custom:{index}"


def template_headings(contract: str) -> List[Tuple[int, str]]:
    found = [(m.start(), int(m.group(1)), m.group(2)) for m in MD_HEADING.finditer(contract)]
    found += [(m.start(), int(m.group(1)), m.group(2)) for m in DOCX_HEADING.finditer(contract)]
    return [(level, title) for _, level, title in sorted(found)]


def match_key(title: str) -> Optional[str]:
    lowered = title.lower()
    for key, words in KEYWORDS:
        if any(word in lowered for word in words):
            return key
    return None


def template_outline(contract: str) -> List[Dict[str, str]]:
    """模板最上层章节 → [{"key", "title"}]；模板没有可用标题时返回空列表（用默认结构）"""
    headings = template_headings(contract)
    if not headings:
        return []
    top = min(level for level, _ in headings)
    tops = [title for level, title in headings if level == top]
    # 只有一个最上层标题、下面还有章节时，它是论文题目，章节从下一级开始
    if len(tops) == 1 and any(level > top for level, _ in headings):
        top = min(level for level, _ in headings if level > top)
        tops = [title for level, title in headings if level == top]

    outline: List[Dict[str, str]] = []
    used = set()
    for index, title in enumerate(tops):
        key = match_key(title)
        if key is None or key in used:
            key = custom_key(index)
        used.add(key)
        outline.append({"key": key, "title": title})

    if not any(item["key"] == "solve_heading" for item in outline):
        # 模板没有建模求解章节时，求解内容放在最后一个"求解之前"的章节后面，否则放最前
        before = {"abstract", "restatement", "analysis", "assumptions", "symbols"}
        position = max((i + 1 for i, item in enumerate(outline) if item["key"] in before), default=0)
        outline.insert(position, {"key": "solve_heading", "title": DEFAULT_SOLVE_TITLE})
    return outline
