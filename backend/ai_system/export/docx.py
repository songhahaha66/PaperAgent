"""
paper.md → paper.docx

- pandoc 把 Markdown 转成 Word，公式转成 Word 原生公式（OMML），表格、列表、图片照常；
- 样式来自参考文档：用户上传了 Word 模板时直接用模板（标题、正文样式、页边距沿用模板），
  否则用按数模论文习惯生成的默认参考文档（正文宋体小四、首行缩进两字，标题黑体、黑色）；
- 章节标题保留"一、问题重述"这样的中文编号（Word 不会自动编号），图题保留"图1 名称"。
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .markdown_prep import (
    drop_caption_lines,
    drop_repeated_subheading,
    expand_math_macros,
    fix_inline_math,
    pandoc,
    require_pandoc,
    run_command,
)

from ai_system.sandbox.commands import sandbox_operation

DEFAULT_REFERENCE = ".build/reference.docx"
BODY_FONT = "宋体"
HEADING_FONT = "黑体"
LATIN_FONT = "Times New Roman"


@dataclass
class DocxResult:
    docx_path: str
    reference: str  # "template" 或 "default"


def _set_fonts(style, east_asia: str, size_pt: Optional[float] = None, bold: Optional[bool] = None) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    font = style.font
    font.name = LATIN_FONT
    font.color.rgb = RGBColor(0, 0, 0)
    color = style.element.rPr.find(qn("w:color"))
    if color is not None:  # pandoc 的标题样式用主题色（蓝色），只设 rgb 会被主题色覆盖
        for attr in ("w:themeColor", "w:themeShade", "w:themeTint"):
            color.attrib.pop(qn(attr), None)
    if size_pt is not None:
        font.size = Pt(size_pt)
    if bold is not None:
        font.bold = bold
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(attr), LATIN_FONT)
    rfonts.set(qn("w:eastAsia"), east_asia)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        rfonts.attrib.pop(qn(attr), None)


async def build_default_reference(workspace: Path) -> Path:
    """从 pandoc 自带的参考文档改出中文论文样式"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    target = workspace / DEFAULT_REFERENCE
    if target.exists():
        return target
    from ai_system.sandbox.commands import restore_reference, save_reference
    if restore_reference(workspace,target):
        return target
    require_pandoc()
    target.parent.mkdir(parents=True, exist_ok=True)
    code, _out, err = await run_command(
        ["pandoc", "-o", str(target), "--print-default-data-file", "reference.docx"], workspace
    )
    if code != 0:
        raise RuntimeError(f"生成参考文档失败：{err.strip()[:300]}")

    doc = Document(str(target))
    # pandoc 的参考文档里样式名是 "Heading 1"，python-docx 按名字取会先转成内置名 "heading 1" 而找不到
    styles = {style.name: style for style in doc.styles}
    for name in ("Normal", "Body Text", "First Paragraph", "Compact"):
        if name in styles:
            _set_fonts(styles[name], BODY_FONT, 12)
    for name in ("Body Text", "First Paragraph"):
        if name in styles:
            fmt = styles[name].paragraph_format
            fmt.first_line_indent = Pt(24)
            fmt.line_spacing = 1.5
            fmt.space_before = Pt(0)
            fmt.space_after = Pt(0)
    heading_sizes = {"Title": 16, "Heading 1": 14, "Heading 2": 12, "Heading 3": 12, "Heading 4": 12}
    for name, size in heading_sizes.items():
        if name in styles:
            _set_fonts(styles[name], HEADING_FONT, size, bold=True)
            styles[name].paragraph_format.space_before = Pt(12)
            styles[name].paragraph_format.space_after = Pt(6)
    for name in ("Title", "Heading 1"):
        if name in styles:
            styles[name].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for name in ("Image Caption", "Table Caption", "Captioned Figure"):
        if name in styles:
            _set_fonts(styles[name], BODY_FONT, 10.5)
            styles[name].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Cm(2.5)
        section.left_margin = section.right_margin = Cm(2.5)
    doc.save(str(target))
    save_reference(workspace,target)
    return target


def prepare_markdown(markdown: str) -> str:
    """标题交给 pandoc 的 title 元数据（Title 样式），其余只做共同整理"""
    markdown = re.sub(r"\A\s*#\s+.+?\n", "", markdown, count=1)
    markdown = expand_math_macros(fix_inline_math(markdown))
    markdown = drop_repeated_subheading(markdown)
    return drop_caption_lines(markdown)


def _title(markdown: str) -> str:
    match = re.match(r"\A\s*#\s+(.+?)\s*$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else ""


def word_inputs(workspace, markdown, docx_name='paper.docx', template_docx=None):
    """Only embedded local assets and the chosen reference cross the API."""
    from ai_system.sandbox.transfer import relative
    names=set()
    # Pandoc raw HTML is disabled. Handle inline and reference-style Markdown images.
    for first,second in re.findall(r'!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))[^)]*\)',markdown):
        names.add(relative(first or second))
    definitions={key.lower():first or second for key,first,second in
        re.findall(r'(?m)^\s*\[([^\]]+)\]:\s*(?:<([^>]+)>|(\S+))',markdown)}
    for label,key in re.findall(r'!\[([^\]]*)\]\[([^\]]*)\]',markdown):
        name=definitions.get((key or label).lower())
        if name:names.add(relative(name))
    if template_docx is not None and Path(template_docx).exists():
        names.add(relative(Path(template_docx).absolute().relative_to(Path(workspace).absolute()).as_posix()))
    return sorted(names)


@sandbox_operation(input_selector=word_inputs)
async def export_docx(
    workspace: Path, markdown: str, docx_name: str = "paper.docx", template_docx: Optional[Path] = None
) -> DocxResult:
    """在工作空间根目录转换（图片路径相对工作空间）"""
    from services.file_services.workspace_layout import safe_join
    workspace = Path(workspace).absolute()
    safe_join(workspace, '.build').mkdir(parents=True, exist_ok=True)
    if template_docx is not None and Path(template_docx).exists():
        relative = Path(template_docx).absolute().relative_to(workspace).as_posix()
        reference, kind = safe_join(workspace, relative), "template"
    else:
        reference, kind = await build_default_reference(workspace), "default"
    args = ["-t", "docx", "-o", ".build/export.docx", "--shift-heading-level-by=-1", f"--reference-doc={reference}"]
    title = _title(markdown)
    if title:
        args += ["-M", f"title={title}"]
    await pandoc(prepare_markdown(markdown), workspace, args)
    from services.file_services.workspace_layout import safe_join
    from services.file_services.file_helper import atomic_write
    atomic_write(safe_join(workspace, docx_name), lambda f: f.write((workspace/".build/export.docx").read_bytes()))
    return DocxResult(docx_path=docx_name, reference=kind)
