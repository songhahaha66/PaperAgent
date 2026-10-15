"""
paper.md → paper.tex → paper.pdf

- 正文由 pandoc 从 Markdown 转成 LaTeX（公式、表格、列表、转义都交给 pandoc），套进数模论文模板 template.tex；
- 标题、摘要、关键词从 Markdown 里拆出来放进模板的封面摘要页；
- 章节编号交给 ctex（"一、问题重述"里的"一、"去掉，避免编号重复），图题编号交给 LaTeX：
  渲染出的 "![图1 名称](path)" 换成 "![名称](path)"，紧随其后的斜体图题行删掉；
- 用 xelatex 编译两遍（交叉引用），中间文件放在 .system/latex/；模型写的公式偶尔用到未定义的命令，
  所以不在第一个错误处停下，只要生成了 PDF 就算成功，错误作为警告返回。

没有 pandoc 时抛 LatexUnavailable；没有 xelatex 时只写 paper.tex，不编译。
"""

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

TEMPLATE_PATH = Path(__file__).with_name("template.tex")
BUILD_DIR = ".system/latex"
COMPILE_TIMEOUT = 180
PANDOC_FROM = "markdown-raw_tex-raw_html-citations+tex_math_dollars"

CHINESE_NUMBER_PREFIX = re.compile(r"^(#{1,6})\s+[一二三四五六七八九十]+[、.．]\s*", re.MULTILINE)
FIGURE_IMAGE = re.compile(r"^!\[(?:图\s*\d+\s*)?(?P<caption>[^\]]*)\]\((?P<path>[^)]+)\)[ \t]*$", re.MULTILINE)
# registry.render 在图片后面加的斜体图题："*图1 名称*"
FIGURE_CAPTION_LINE = re.compile(r"^(?P<image>!\[[^\]]*\]\([^)]+\))[ \t]*\n\n\*图\s*\d+[^\n]*\*[ \t]*$", re.MULTILINE)
KEYWORDS_LINE = re.compile(r"^\s*(?:\*\*)?关键词(?:\*\*)?\s*[：:]\s*(?:\*\*)?\s*(?P<words>.+?)\s*$", re.MULTILINE)
MATH_SPAN = re.compile(r"\$\$.*?\$\$|\$[^$\n]+\$", re.DOTALL)
NUMBER_AFTER = re.compile(r"\s*(-?\d[\d.,]*(?:[eE][-+]?\d+)?)(%?)")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
LOG_ERROR = re.compile(r"^! .*$", re.MULTILINE)


class LatexUnavailable(RuntimeError):
    """缺少 pandoc，无法生成 LaTeX"""


@dataclass
class LatexResult:
    tex_path: str
    pdf_path: Optional[str] = None
    pages: Optional[int] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


@dataclass
class PaperParts:
    title: str
    abstract: str
    keywords: str
    body: str


def split_paper(markdown: str) -> PaperParts:
    """拆出标题、摘要、关键词和正文；正文的章节从 ## 开始"""
    title = ""
    match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    if match:
        title = match.group(1).strip()
        markdown = markdown[: match.start()] + markdown[match.end():]

    abstract, keywords = "", ""
    match = re.search(r"^##\s+摘\s*要\s*$", markdown, re.MULTILINE)
    if match:
        nxt = re.search(r"^##\s+", markdown[match.end():], re.MULTILINE)
        end = match.end() + nxt.start() if nxt else len(markdown)
        abstract = markdown[match.end():end].strip()
        markdown = markdown[: match.start()] + markdown[end:]
        kw = KEYWORDS_LINE.search(abstract)
        if kw:
            keywords = kw.group("words").strip().strip("*").strip()
            abstract = (abstract[: kw.start()] + abstract[kw.end():]).strip()
    return PaperParts(title=title, abstract=abstract, keywords=keywords, body=markdown.strip())


def drop_repeated_subheading(markdown: str) -> str:
    """章节正文第一行又写了一遍同名小标题（"## 六、灵敏度分析" 下面紧跟 "### 灵敏度分析"）时删掉"""
    lines = markdown.split("\n")
    out: List[str] = []
    parent: Optional[str] = None
    for line in lines:
        match = HEADING.match(line)
        if match:
            title = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", match.group(2)).strip()
            if parent is not None and title == parent:
                parent = None
                continue
            parent = title
            out.append(line)
            continue
        if line.strip():
            parent = None
        out.append(line)
    return "\n".join(out)


def fix_inline_math(markdown: str) -> str:
    """pandoc 不认结束符前有空格的行内公式（"$\\beta_1 = $"），按顺序配对后去掉公式内的尾部空格；
    写作模型常把引用的数值放在公式外（"$\\beta_1 = $ 0.8027"），以等号结尾的公式把紧跟的数字并回去"""
    out: List[str] = []
    cursor = 0
    for match in MATH_SPAN.finditer(markdown):
        if match.start() < cursor:
            continue
        span = match.group(0)
        out.append(markdown[cursor:match.start()])
        cursor = match.end()
        if span.startswith("$$"):
            out.append(span)
            continue
        inner = span[1:-1].strip()
        number = NUMBER_AFTER.match(markdown, cursor) if inner.endswith("=") else None
        if number:
            inner = f"{inner} {number.group(1)}{chr(92) + '%' if number.group(2) else ''}"
            cursor = number.end()
        out.append(f"${inner}$")
    out.append(markdown[cursor:])
    return "".join(out)


def prepare_markdown(markdown: str) -> str:
    """交给 pandoc 之前的整理：编号、图题交给 LaTeX，修正行内公式"""
    markdown = fix_inline_math(markdown)
    markdown = drop_repeated_subheading(markdown)
    markdown = CHINESE_NUMBER_PREFIX.sub(lambda m: f"{m.group(1)} ", markdown)
    markdown = FIGURE_CAPTION_LINE.sub(lambda m: m.group("image"), markdown)
    return FIGURE_IMAGE.sub(lambda m: f"![{m.group('caption').strip()}]({m.group('path')})", markdown)


async def _run(cmd: List[str], cwd: Path, stdin: Optional[str] = None, timeout: int = COMPILE_TIMEOUT):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode("utf-8") if stdin is not None else None), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"{cmd[0]} 超时（{timeout} 秒）")
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def pandoc_to_latex(markdown: str, cwd: Path) -> str:
    if not shutil.which("pandoc"):
        raise LatexUnavailable("服务器没有安装 pandoc，无法生成 LaTeX")
    code, out, err = await _run(
        ["pandoc", "-f", PANDOC_FROM, "-t", "latex", "--wrap=preserve", "--shift-heading-level-by=-1"],
        cwd,
        stdin=markdown,
    )
    if code != 0:
        raise RuntimeError(f"pandoc 转换失败：{err.strip()[:500]}")
    return out


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
        _code, out, _err = await _run(cmd, workspace)
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
