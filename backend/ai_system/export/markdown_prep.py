"""
导出前对渲染好的 paper.md 做的共同整理（LaTeX 和 Word 共用），以及调用 pandoc 的工具函数
"""

import asyncio
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

PANDOC_FROM = "markdown-raw_tex-raw_html-citations+tex_math_dollars"
PANDOC_TIMEOUT = 180

CHINESE_NUMBER_PREFIX = re.compile(r"^(#{1,6})\s+[一二三四五六七八九十]+[、.．]\s*", re.MULTILINE)
FIGURE_IMAGE = re.compile(r"^!\[(?:图\s*\d+\s*)?(?P<caption>[^\]]*)\]\((?P<path>[^)]+)\)[ \t]*$", re.MULTILINE)
# registry.render 在图片后面加的斜体图题："*图1 名称*"
FIGURE_CAPTION_LINE = re.compile(r"^(?P<image>!\[[^\]]*\]\([^)]+\))[ \t]*\n\n\*图\s*\d+[^\n]*\*[ \t]*$", re.MULTILINE)
KEYWORDS_LINE = re.compile(r"^\s*(?:\*\*)?关键词(?:\*\*)?\s*[：:]\s*(?:\*\*)?\s*(?P<words>.+?)\s*$", re.MULTILINE)
MATH_SPAN = re.compile(r"\$\$.*?\$\$|\$[^$\n]+\$", re.DOTALL)
NUMBER_AFTER = re.compile(r"\s*(-?\d[\d.,]*(?:[eE][-+]?\d+)?)(%?)")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)

# 写作模型常用、但不是标准 LaTeX 的算子。LaTeX 模板里用 \providecommand 定义；
# Word 公式由 pandoc 转换，不认识这些命令，导出前展开
MATH_MACROS = {
    "logit": r"\operatorname{logit}",
    "argmax": r"\operatorname*{arg\,max}",
    "argmin": r"\operatorname*{arg\,min}",
    "sgn": r"\operatorname{sgn}",
    "sign": r"\operatorname{sign}",
    "Var": r"\operatorname{Var}",
    "Cov": r"\operatorname{Cov}",
    "Corr": r"\operatorname{Corr}",
    "E": r"\mathbb{E}",
    "R": r"\mathbb{R}",
    "N": r"\mathbb{N}",
    "diag": r"\operatorname{diag}",
    "tr": r"\operatorname{tr}",
    "softmax": r"\operatorname{softmax}",
    "Bern": r"\operatorname{Bernoulli}",
    "given": r"\mid",
}
MACRO_PATTERN = re.compile(r"\\(" + "|".join(sorted(MATH_MACROS, key=len, reverse=True)) + r")(?![A-Za-z])")


class PandocUnavailable(RuntimeError):
    """缺少 pandoc，无法导出 LaTeX / Word"""


def drop_caption_lines(markdown: str) -> str:
    """registry.render 在图片后面又写了一行斜体图题，导出时图题由图片本身承担"""
    return FIGURE_CAPTION_LINE.sub(lambda m: m.group("image"), markdown)


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


def expand_math_macros(markdown: str) -> str:
    """只在公式里展开 MATH_MACROS"""
    return MATH_SPAN.sub(lambda m: MACRO_PATTERN.sub(lambda c: MATH_MACROS[c.group(1)], m.group(0)), markdown)


async def run_command(cmd: List[str], cwd: Path, stdin: Optional[str] = None, timeout: int = PANDOC_TIMEOUT):
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


def require_pandoc() -> None:
    if not shutil.which("pandoc"):
        raise PandocUnavailable("服务器没有安装 pandoc，无法导出")


async def pandoc(markdown: str, cwd: Path, args: List[str]) -> str:
    """markdown 从 stdin 进 pandoc，返回 stdout（-o 写文件时为空）"""
    require_pandoc()
    code, out, err = await run_command(["pandoc", "-f", PANDOC_FROM, *args], cwd, stdin=markdown)
    if code != 0:
        raise RuntimeError(f"pandoc 转换失败：{err.strip()[:500]}")
    return out
