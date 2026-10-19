"""Native LaTeX document assembly and PDF compilation. No Markdown conversion."""

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .markdown_prep import run_command

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


def escape_plain(text: str) -> str:
    """Escape registered plain text, preserving authored TeX elsewhere."""
    table = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
        "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    return "".join(table.get(ch, ch) for ch in text)


def native_document(title: str, abstract: str, body: str) -> str:
    """Join native section fragments into the built-in ctex document."""
    values = {"TITLE": escape_plain(title), "ABSTRACT": abstract.strip(), "BODY": body.strip()}
    return re.sub(r"<<(TITLE|ABSTRACT|BODY)>>", lambda m: values[m.group(1)], TEMPLATE_PATH.read_text(encoding="utf-8"))


def fragment_issues(text: str) -> List[str]:
    """Deterministic checks fed to the existing section repair loop."""
    issues = []
    if re.search(r"(?m)^\s*(?:#{1,6}\s|```|\|.*\|\s*$)", text) or re.search(r"!\[[^\]]*\]\(", text):
        issues.append("LaTeX 模式不能使用 Markdown 标题、表格、图片或代码围栏，请改成原生 LaTeX。")
    if re.search(r"\\(?:documentclass|usepackage|input|include|includegraphics|write|openout|read|newcommand|renewcommand)\b|\\(?:begin|end)\{document\}", text):
        issues.append("只输出本节 LaTeX 正文，不要输出导言区、文档环境、文件操作或图片路径；插图使用登记图引用。")
    if re.search(r"\\begin\{figure\*?\}", text):
        issues.append("登记图引用必须独占一行，不要再包 figure 环境或添加图题和图标签；系统会生成唯一的 figure、caption 和 label。")
    depth = 0
    clean = re.sub(r"(?<!\\)%[^\n]*", "", text)
    for token in re.findall(r"(?<!\\)[{}]", clean):
        depth += 1 if token == "{" else -1
        if depth < 0:
            break
    if depth:
        issues.append("LaTeX 花括号未配对。")
    stack = []
    for command, env in re.findall(r"\\(begin|end)\{([^{}]+)\}", clean):
        if command == "begin":
            stack.append(env)
        elif not stack or stack.pop() != env:
            issues.append("LaTeX 环境未配对。")
            break
    else:
        if stack:
            issues.append("LaTeX 环境未配对。")
    return issues


def _log_errors(log: str) -> List[str]:
    return [line.strip() for line in LOG_ERROR.findall(log)][:5]


async def compile_pdf(workspace: Path, tex_name: str = "paper.tex") -> LatexResult:
    """在工作空间根目录编译（图片路径相对工作空间），中间文件放 .system/latex/，PDF 复制到根目录"""
    result = LatexResult(tex_path=tex_name)
    target = workspace / f"{Path(tex_name).stem}.pdf"
    if not shutil.which("xelatex"):
        result.error = "服务器没有安装 xelatex，只生成了 paper.tex"
        return result
    build = workspace / BUILD_DIR
    build.mkdir(parents=True, exist_ok=True)
    stem = Path(tex_name).stem
    pdf = build / f"{stem}.pdf"
    pdf.unlink(missing_ok=True)
    cmd = ["xelatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={BUILD_DIR}", tex_name]
    out = ""
    for iteration in range(3):  # bibliography, then resolve cross-references
        code, out, _err = await run_command(cmd, workspace, timeout=COMPILE_TIMEOUT)
        if code != 0:
            break
        if iteration == 0:
            aux = build / f"{stem}.aux"
            if aux.exists() and r'\bibdata{' in aux.read_text(errors='replace'):
                if not shutil.which('bibtex'):
                    result.error = '缺少 bibtex，保留上次成功 PDF；请安装后重新编译'
                    return result
                code, out, _err = await run_command(['bibtex', f'{BUILD_DIR}/{stem}'], workspace, timeout=COMPILE_TIMEOUT)
                if code != 0:
                    result.error = '参考文献编译失败：' + out.strip()[-500:]
                    return result
    log_file = build / f"{stem}.log"
    log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else out
    errors = _log_errors(log)
    if "There were undefined references" in log or "There were undefined citations" in log:
        errors.append("存在未定义的交叉引用")
    if code != 0 or errors or not pdf.exists():
        result.error = "xelatex 编译失败：" + ("；".join(errors) if errors else out.strip()[-500:])
        return result

    shutil.copyfile(pdf, target)
    result.pdf_path = target.name
    pages = re.search(r"Output written on .*?\((\d+) pages?", log, re.DOTALL)
    result.pages = int(pages.group(1)) if pages else None
    missing = sorted(set(re.findall(r"Missing character: There is no (\S+)", log)))
    result.warnings = errors + ([f"缺字形：{' '.join(missing[:10])}"] if missing else [])
    return result
