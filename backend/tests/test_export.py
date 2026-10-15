"""论文导出（LaTeX / Word）与模板章节结构"""

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.export.docx import export_docx
from ai_system.export.latex import markdown_to_tex, prepare_markdown
from ai_system.export.markdown_prep import expand_math_macros, fix_inline_math, split_paper
from ai_system.graph import PaperGraphAgent
from ai_system.graph.outline import template_outline
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_paper_graph import TASK, FakeModel, FixedJudge, _make_workspace

needs_pandoc = pytest.mark.skipif(not shutil.which("pandoc"), reason="没有 pandoc")
needs_xelatex = pytest.mark.skipif(not shutil.which("xelatex"), reason="没有 xelatex")

PAPER = """# 用电量预测模型

## 摘要

本文建立了预测模型，准确率为 92.5%。

**关键词**：时间序列；回归

## 一、问题重述

### 问题背景

背景说明，费用 50%。

## 六、灵敏度分析

### 灵敏度分析

系数 $\\beta_1 = $ 0.8027，截距 $\\beta_0 = $ -0.3396。

![图1 年度用电量](outputs/figures/F1.png)

*图1 年度用电量*

如图1所示。
"""


def test_split_paper_extracts_title_abstract_keywords():
    parts = split_paper(PAPER)
    assert parts.title == "用电量预测模型"
    assert parts.abstract == "本文建立了预测模型，准确率为 92.5%。"
    assert parts.keywords == "时间序列；回归"
    assert parts.body.startswith("## 一、问题重述") and "摘要" not in parts.body


def test_prepare_markdown_leaves_numbering_to_latex():
    body = prepare_markdown(split_paper(PAPER).body)
    # 章节编号交给 ctex，同名小标题删掉
    assert "## 问题重述" in body and "## 灵敏度分析" in body and "### 灵敏度分析" not in body
    assert "### 问题背景" in body
    # 图题编号交给 LaTeX，重复的斜体图题行删掉
    assert "![年度用电量](outputs/figures/F1.png)" in body and "*图1" not in body
    assert "$\\beta_1 = 0.8027$" in body and "$\\beta_0 = -0.3396$" in body


def test_fix_inline_math_pairs_in_order():
    assert fix_inline_math("占比 $p = $ 62.6%") == "占比 $p = 62.6\\%$"
    # 按顺序配对：前一个公式的结束符不会和下一个公式的开始符配成一对
    assert fix_inline_math("$a + b $ 和 $c$") == "$a + b$ 和 $c$"
    assert fix_inline_math("$$x = 1 $$ 后 $ y$") == "$$x = 1 $$ 后 $y$"


@needs_pandoc
def test_markdown_to_tex_fills_template(tmp_path):
    tex = asyncio.run(markdown_to_tex(PAPER, tmp_path))
    assert "\\documentclass" in tex and "<<" not in tex
    assert "用电量预测模型" in tex and "\\textbf{关键词：}时间序列；回归" in tex
    assert "准确率为 92.5\\%" in tex
    assert "\\section{问题重述}" in tex and "\\subsection{问题背景}" in tex
    assert "\\caption{年度用电量}" in tex
    assert tex.index("摘") < tex.index("\\section{问题重述}")


@needs_pandoc
@needs_xelatex
def test_graph_agent_latex_mode_builds_pdf(tmp_path):
    ws = _make_workspace(tmp_path)
    events = []

    class Recorder:
        async def send_json_block(self, block_type, content):
            events.append((block_type, content))

    agent = PaperGraphAgent(
        FakeModel(), stream_manager=Recorder(), workspace_dir=str(ws), judge=FixedJudge(), output_mode="latex"
    )
    output = asyncio.run(agent.run(TASK))

    assert (ws / "paper.md").exists() and (ws / "paper.tex").exists()
    assert (ws / "paper.pdf").read_bytes().startswith(b"%PDF")
    assert "paper.pdf" in output
    assert any(t == "paper_phase" and c["stage"] == "export" and c["status"] == "completed" for t, c in events)
    assert any(t == "paper_export" and "paper.pdf" in c for t, c in events)
    assert "LaTeX 排版" in (ws / "plan.md").read_text(encoding="utf-8")


def test_expand_math_macros_only_inside_math():
    text = "正文 \\E 不动 $\\logit(p) = \\E[x] + \\Exp$"
    assert expand_math_macros(text) == "正文 \\E 不动 $\\operatorname{logit}(p) = \\mathbb{E}[x] + \\Exp$"


@needs_pandoc
def test_export_docx_with_default_style(tmp_path):
    from docx import Document

    result = asyncio.run(export_docx(tmp_path, PAPER))
    assert result.reference == "default"
    doc = Document(str(tmp_path / "paper.docx"))
    styles = [p.style.name for p in doc.paragraphs]
    texts = [p.text for p in doc.paragraphs]
    assert texts[0] == "用电量预测模型" and styles[0] == "Title"
    assert "一、问题重述" in texts and "Heading 1" in styles
    # 公式转成 Word 原生公式；数在公式外的写法先并回公式
    assert "<m:oMath" in doc.element.xml
    # 默认样式：标题不再是 pandoc 的蓝色主题色
    reference = Document(str(tmp_path / ".system" / "export" / "reference.docx"))
    heading = {s.name: s for s in reference.styles}["Heading 1"]
    assert "themeColor" not in heading.element.rPr.xml


@needs_pandoc
def test_export_docx_uses_template_styles(tmp_path):
    from docx import Document
    from docx.shared import Pt

    template = Document()
    template.styles["Normal"].font.size = Pt(15)
    template.save(str(tmp_path / "template.docx"))
    result = asyncio.run(export_docx(tmp_path, PAPER, template_docx=tmp_path / "template.docx"))
    assert result.reference == "template"
    assert Document(str(tmp_path / "paper.docx")).styles["Normal"].font.size == Pt(15)


MD_CONTRACT = """# 模板契约

## Markdown 骨架
- L1 第1行: 论文题目
- L2 第3行: 摘要
- L2 第5行: 1 研究背景
- L2 第7行: 2 模型假设
- L2 第9行: 3 模型建立与求解
- L3 第10行: 3.1 问题一
- L2 第12行: 4 结论与建议
- L2 第14行: 参考文献
"""


def test_template_outline_maps_headings_to_sections():
    outline = template_outline(MD_CONTRACT)
    assert outline == [
        {"key": "abstract", "title": "摘要"},
        {"key": "restatement", "title": "1 研究背景"},
        {"key": "assumptions", "title": "2 模型假设"},
        {"key": "solve_heading", "title": "3 模型建立与求解"},
        {"key": "custom:4", "title": "4 结论与建议"},
        {"key": "custom:5", "title": "参考文献"},
    ]


def test_template_outline_docx_headings_and_missing_solve():
    contract = "## Word 骨架\n- 第1段 [Title]: 题目\n- 第2段 [Heading 1]: 摘要\n- 第3段 [Normal]: 正文\n- 第4段 [Heading 1]: 问题重述\n- 第5段 [Heading 1]: 致谢\n"
    outline = template_outline(contract)
    # 模板没有求解章节时，求解内容插在"求解之前"的章节后面
    assert [item["key"] for item in outline] == ["abstract", "restatement", "solve_heading", "custom:2"]
    assert template_outline("## Word 骨架\n- 第1段 [Normal]: 正文\n") == []


@needs_pandoc
def test_graph_agent_word_mode_follows_template(tmp_path):
    from docx import Document

    ws = _make_workspace(tmp_path)
    (ws / ".system").mkdir(parents=True, exist_ok=True)
    (ws / ".system" / "template_contract.md").write_text(MD_CONTRACT, encoding="utf-8")
    agent = PaperGraphAgent(FakeModel(), workspace_dir=str(ws), judge=FixedJudge(), output_mode="word")
    output = asyncio.run(agent.run(TASK))

    paper = (ws / "paper.md").read_text(encoding="utf-8")
    headings = [line for line in paper.splitlines() if line.startswith("## ")]
    assert headings == ["## 摘要", "## 1 研究背景", "## 2 模型假设", "## 3 模型建立与求解", "## 4 结论与建议", "## 参考文献"]
    assert "### Q1" in paper
    assert "paper.docx" in output
    texts = [p.text for p in Document(str(ws / "paper.docx")).paragraphs]
    assert "4 结论与建议" in texts
    assert "Word" in (ws / "plan.md").read_text(encoding="utf-8")
