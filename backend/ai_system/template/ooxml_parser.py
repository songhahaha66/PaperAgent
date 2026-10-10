"""Shared OOXML document parser.

Detects headings from style name, inherited outlineLvl, numbering, and
common Chinese custom heading style names — not only `heading*`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
import re
import zipfile

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
W = f"{{{W_NS}}}"

CUSTOM_HEADING_RE = re.compile(
    r"(一级标题|二级标题|三级标题|四级标题|章标题|节标题|标题\s*[一二三四五1-5])"
)


@dataclass
class StyleDef:
    style_id: str
    name: str
    based_on: str | None = None
    outline_lvl: int | None = None
    bold: bool | None = None
    size_pt: float | None = None
    numbered: bool = False


@dataclass
class ResolvedStyle:
    name: str
    outline_lvl: int | None = None
    bold: bool | None = None
    size_pt: float | None = None
    numbered: bool = False


@dataclass
class ParsedBlock:
    id: str
    kind: str
    text: str
    style: str | None = None
    outline_level: int | None = None
    numbered: bool = False
    bold: bool | None = None
    size_pt: float | None = None
    is_heading: bool = False
    table_rows: list[list[str]] = field(default_factory=list)
    image_count: int = 0


@dataclass
class ParsedDocument:
    blocks: list[ParsedBlock]
    headings: list[tuple[str, str]]
    paragraphs: list[dict[str, str]]
    text: str
    table_count: int
    media_count: int
    has_header: bool
    has_footer: bool
    media_names: list[str]


def parse_docx(docx_path: Path | str) -> ParsedDocument:
    docx_path = Path(docx_path)
    with zipfile.ZipFile(docx_path) as archive:
        document_root = ET.fromstring(archive.read("word/document.xml"))
        styles = _load_styles(archive)
        media_names = [name for name in archive.namelist() if name.startswith("word/media/")]
        has_header = any(name.startswith("word/header") for name in archive.namelist())
        has_footer = any(name.startswith("word/footer") for name in archive.namelist())

    body = document_root.find("w:body", NS)
    blocks: list[ParsedBlock] = []
    headings: list[tuple[str, str]] = []
    paragraphs: list[dict[str, str]] = []
    paragraph_index = 0
    table_index = 0
    figure_index = 0
    table_count = 0

    children = list(body) if body is not None else []
    for child in children:
        if child.tag == f"{W}tbl":
            table_count += 1
            rows = _table_cells(child)
            preview = " / ".join(cell for row in rows[:2] for cell in row[:4] if cell)
            block = ParsedBlock(
                id=f"T{table_index:03d}",
                kind="table",
                text=preview,
                table_rows=rows,
            )
            table_index += 1
            blocks.append(block)
            continue
        if child.tag != f"{W}p":
            continue

        text = "".join(node.text or "" for node in child.findall(".//w:t", NS)).strip()
        style_id = _attr(child.find("./w:pPr/w:pStyle", NS), "val")
        resolved = _resolve_style(style_id, styles)
        p_outline = _int_attr(child.find("./w:pPr/w:outlineLvl", NS), "val")
        p_num = child.find("./w:pPr/w:numPr", NS) is not None
        rpr = child.find("./w:pPr/w:rPr", NS)
        run_rpr = child.find("./w:r/w:rPr", NS)
        bold = _bool_flag(rpr, "b")
        if bold is None:
            bold = _bool_flag(run_rpr, "b")
        size_pt = _sz_pt(rpr) or _sz_pt(run_rpr)
        outline_level = p_outline if p_outline is not None else resolved.outline_lvl
        numbered = p_num or resolved.numbered
        image_count = len(child.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline"))
        heading = _is_heading(resolved, outline_level, numbered, text)
        if image_count and not text:
            block = ParsedBlock(
                id=f"G{figure_index:03d}",
                kind="figure",
                text="",
                image_count=image_count,
            )
            figure_index += 1
        else:
            block = ParsedBlock(
                id=f"P{paragraph_index:03d}",
                kind="paragraph",
                text=text,
                style=resolved.name or style_id or None,
                outline_level=outline_level,
                numbered=numbered,
                bold=resolved.bold if bold is None else bold,
                size_pt=size_pt or resolved.size_pt,
                is_heading=heading,
                image_count=image_count,
            )
            paragraph_index += 1
        blocks.append(block)
        if text:
            paragraphs.append({"text": text, "style": block.style or ""})
        if heading and text:
            headings.append(((block.style or "").lower(), text))

    return ParsedDocument(
        blocks=blocks,
        headings=headings,
        paragraphs=paragraphs,
        text="\n".join(item["text"] for item in paragraphs),
        table_count=table_count,
        media_count=len(media_names),
        has_header=has_header,
        has_footer=has_footer,
        media_names=media_names,
    )


def docx_outline(docx_path: Path | str) -> dict[str, Any]:
    parsed = parse_docx(docx_path)
    return {
        "paragraphs": parsed.paragraphs,
        "headings": parsed.headings,
        "text": parsed.text,
        "table_count": parsed.table_count,
        "media_count": parsed.media_count,
        "blocks": parsed.blocks,
        "has_header": parsed.has_header,
        "has_footer": parsed.has_footer,
        "media_names": parsed.media_names,
    }


def _load_styles(archive: zipfile.ZipFile) -> dict[str, StyleDef]:
    styles: dict[str, StyleDef] = {}
    if "word/styles.xml" not in archive.namelist():
        return styles
    root = ET.fromstring(archive.read("word/styles.xml"))
    for style in root.findall("w:style", NS):
        style_id = style.attrib.get(f"{W}styleId", "")
        name_el = style.find("w:name", NS)
        name = _attr(name_el, "val") or style_id
        based_on = _attr(style.find("w:basedOn", NS), "val")
        outline = _int_attr(style.find("./w:pPr/w:outlineLvl", NS), "val")
        bold = _bool_flag(style.find("./w:rPr", NS), "b")
        size_pt = _sz_pt(style.find("./w:rPr", NS))
        numbered = style.find("./w:pPr/w:numPr", NS) is not None
        styles[style_id] = StyleDef(
            style_id=style_id,
            name=name,
            based_on=based_on,
            outline_lvl=outline,
            bold=bold,
            size_pt=size_pt,
            numbered=numbered,
        )
    return styles


def _resolve_style(style_id: str, styles: dict[str, StyleDef]) -> ResolvedStyle:
    if not style_id:
        return ResolvedStyle(name="")
    seen: set[str] = set()
    name = style_id
    outline = None
    bold = None
    size_pt = None
    numbered = False
    current = style_id
    while current and current not in seen:
        seen.add(current)
        info = styles.get(current)
        if info is None:
            break
        name = info.name or name
        if outline is None:
            outline = info.outline_lvl
        if bold is None:
            bold = info.bold
        if size_pt is None:
            size_pt = info.size_pt
        numbered = numbered or info.numbered
        current = info.based_on or ""
    return ResolvedStyle(name=name, outline_lvl=outline, bold=bold, size_pt=size_pt, numbered=numbered)


def _is_heading(resolved: ResolvedStyle, outline_level: int | None, numbered: bool, text: str) -> bool:
    if not text:
        return False
    if outline_level is not None and 0 <= outline_level <= 8:
        return True
    name = resolved.name or ""
    lowered = name.lower()
    if lowered.startswith("heading") or lowered in {"title", "subtitle"}:
        return True
    if CUSTOM_HEADING_RE.search(name):
        return True
    if name.startswith("标题") and (resolved.bold or numbered):
        return True
    return False


def _table_cells(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall("w:tr", NS):
        cells = []
        for cell in row.findall("w:tc", NS):
            text = "".join(node.text or "" for node in cell.findall(".//w:t", NS)).strip()
            cells.append(text)
        rows.append(cells)
    return rows


def _attr(element: ET.Element | None, name: str) -> str:
    if element is None:
        return ""
    return element.attrib.get(f"{W}{name}", "")


def _int_attr(element: ET.Element | None, name: str) -> int | None:
    raw = _attr(element, name)
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _bool_flag(rpr: ET.Element | None, tag: str) -> bool | None:
    if rpr is None:
        return None
    node = rpr.find(f"w:{tag}", NS)
    if node is None:
        return None
    val = node.attrib.get(f"{W}val")
    if val in {None, "1", "true", "on"}:
        return True
    if val in {"0", "false", "off"}:
        return False
    return True


def _sz_pt(rpr: ET.Element | None) -> float | None:
    if rpr is None:
        return None
    node = rpr.find("w:sz", NS)
    raw = _attr(node, "val")
    if not raw:
        return None
    try:
        return int(raw) / 2
    except ValueError:
        return None
