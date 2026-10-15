"""LaTeX 排版：paper.md → paper.tex → paper.pdf"""

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph import PaperGraphAgent
from ai_system.latex.convert import fix_inline_math, markdown_to_tex, prepare_markdown, split_paper
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
    assert any(t == "paper_phase" and c["stage"] == "typeset" and c["status"] == "completed" for t, c in events)
    assert any(t == "paper_typeset" and "paper.pdf" in c for t, c in events)
    assert "LaTeX 排版" in (ws / "plan.md").read_text(encoding="utf-8")
