"""
paper.md → paper.tex → paper.pdf

- 正文由 pandoc 从 Markdown 转成 LaTeX（公式、表格、列表、转义都交给 pandoc），套进数模论文模板 template.tex；
- 标题、摘要、关键词从 Markdown 里拆出来放进模板的封面摘要页；
- 章节编号交给 ctex（"一、问题重述"里的"一、"去掉，避免编号重复），图题编号交给 LaTeX：
  渲染出的 "![图1 名称](path)" 换成 "![名称](path)"，紧随其后的斜体图题行删掉；
- 用 xelatex 编译两遍（交叉引用），中间文件放在 .system/latex/；模型写的公式偶尔用到未定义的命令，
  所以不在第一个错误处停下，只要生成了 PDF 就算成功，错误作为警告返回。

没有 pandoc 时抛 PandocUnavailable；没有 xelatex 时只写 paper.tex，不编译。
"""

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .markdown_prep import (
    CHINESE_NUMBER_PREFIX,
    FIGURE_IMAGE,
    drop_caption_lines,
    drop_repeated_subheading,
    fix_inline_math,
    pandoc,
    run_command,
    split_paper,
)

TEMPLATE_PATH = Path(__file__).with_name("template.tex")
BUILD_DIR = ".system/latex"
COMPILE_TIMEOUT = 180

LOG_ERROR = re.compile(r"^! .*$", re.MULTILINE)


@dataclass
class LatexResult:
    tex_path: str
    pdf_path: Optional[str] = None
    pages: Optional[int] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


async def pandoc_to_latex(markdown: str, cwd: Path) -> str:
    return await pandoc(markdown, cwd, ["-t", "latex", "--wrap=preserve", "--shift-heading-level-by=-1"])


def prepare_markdown(markdown: str) -> str:
    """交给 pandoc 之前的整理：编号、图题交给 LaTeX，修正行内公式"""
    markdown = fix_inline_math(markdown)
    markdown = drop_repeated_subheading(markdown)
    markdown = CHINESE_NUMBER_PREFIX.sub(lambda m: f"{m.group(1)} ", markdown)
    markdown = drop_caption_lines(markdown)
    return FIGURE_IMAGE.sub(lambda m: f"![{m.group('caption').strip()}]({m.group('path')})", markdown)


def escape_plain(text: str) -> str:
    """标题、关键词这类纯文本的 LaTeX 转义"""
    table = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
        "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    return "".join(table.get(ch, ch) for ch in text)


async def markdown_to_tex(markdown: str, cwd: Path) -> str:
    parts = split_paper(markdown)
    abstract = await pandoc_to_latex(fix_inline_math(parts.abstract), cwd) if parts.abstract else ""
    body = await pandoc_to_latex(prepare_markdown(parts.body), cwd)
    keywords = ""
    if parts.keywords:
        keywords = "\\vspace{1ex}\n\\noindent\\textbf{关键词：}" + escape_plain(parts.keywords)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    # 依次替换，内容里即使出现占位符也不会被二次替换
    for key, value in (
        ("<<TITLE>>", escape_plain(parts.title or "数学建模论文")),
        ("<<ABSTRACT>>", abstract.strip()),
        ("<<KEYWORDS>>", keywords),
        ("<<BODY>>", body.strip()),
    ):
        head, _, tail = template.partition(key)
        template = head + value + tail
    return template


def _log_errors(log: str) -> List[str]:
    return [line.strip() for line in LOG_ERROR.findall(log)][:5]


async def compile_pdf(workspace: Path, tex_name: str = "paper.tex") -> LatexResult:
    """在工作空间根目录编译（图片路径相对工作空间），中间文件放 .system/latex/，PDF 复制到根目录"""
    result = LatexResult(tex_path=tex_name)
    if not shutil.which("xelatex"):
        result.error = "服务器没有安装 xelatex，只生成了 paper.tex"
        return result
    build = workspace / BUILD_DIR
    build.mkdir(parents=True, exist_ok=True)
    stem = Path(tex_name).stem
    pdf = build / f"{stem}.pdf"
    pdf.unlink(missing_ok=True)
    cmd = ["xelatex", "-interaction=nonstopmode", f"-output-directory={BUILD_DIR}", tex_name]
    out = ""
    for _ in range(2):  # 第二遍解析交叉引用
        _code, out, _err = await run_command(cmd, workspace, timeout=COMPILE_TIMEOUT)
    log_file = build / f"{stem}.log"
    log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else out
    errors = _log_errors(log)
    if not pdf.exists():
        result.error = "xelatex 编译失败：" + ("；".join(errors) if errors else out.strip()[-500:])
        return result

    target = workspace / f"{stem}.pdf"
    shutil.copyfile(pdf, target)
    result.pdf_path = target.name
    pages = re.search(r"Output written on .*?\((\d+) pages?", log, re.DOTALL)
    result.pages = int(pages.group(1)) if pages else None
    missing = sorted(set(re.findall(r"Missing character: There is no (\S+)", log)))
    result.warnings = errors + ([f"缺字形：{' '.join(missing[:10])}"] if missing else [])
    return result


async def typeset(workspace: Path, markdown: str, tex_name: str = "paper.tex") -> LatexResult:
    """paper.md 内容 → workspace/paper.tex（+ paper.pdf）"""
    workspace = Path(workspace)
    tex = await markdown_to_tex(markdown, workspace)
    (workspace / tex_name).write_text(tex, encoding="utf-8")
    return await compile_pdf(workspace, tex_name)
