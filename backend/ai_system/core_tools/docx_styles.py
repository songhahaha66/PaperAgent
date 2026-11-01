"""Extract Word template style fingerprints for the document contract."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET
import zipfile

logger = logging.getLogger(__name__)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}

PROFILE_PATH = "template/style_profile.json"
PRIORITY_STYLES = (
    "Normal",
    "Title",
    "Subtitle",
    "Heading 1",
    "Heading 2",
    "Heading 3",
    "Heading 4",
)

@dataclass
class StyleFingerprint:
    page: Dict[str, Optional[float]] = field(default_factory=dict)
    styles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    has_header: bool = False
    has_footer: bool = False
    heading_samples: List[Dict[str, Any]] = field(default_factory=list)
    body_samples: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def format_report(self, title: str = "样式档案") -> str:
        page = self.page
        lines = [
            f"## {title}",
            (
                f"- 页面: {page.get('width_pt')}×{page.get('height_pt')} pt, "
                f"边距 上{page.get('top_pt')}/下{page.get('bottom_pt')}/"
                f"左{page.get('left_pt')}/右{page.get('right_pt')} pt"
            ),
            f"- 页眉: {'有' if self.has_header else '无'}；页脚: {'有' if self.has_footer else '无'}",
            "",
            "### 样式定义",
        ]
        if self.styles:
            for name, attrs in self.styles.items():
                lines.append(f"- {name}: {_format_attrs(attrs)}")
        else:
            lines.append("- 未读到可用样式定义")

        if self.heading_samples:
            lines.append("\n### 标题样例（实际段落）")
            for sample in self.heading_samples[:8]:
                lines.append(
                    f"- [{sample.get('style')}] {sample.get('text')}: {_format_attrs(sample)}"
                )
        if self.body_samples:
            lines.append("\n### 正文样例（实际段落）")
            for sample in self.body_samples[:6]:
                lines.append(
                    f"- [{sample.get('style')}] {sample.get('text')}: {_format_attrs(sample)}"
                )
        return "\n".join(lines) + "\n"


def extract_style_fingerprint(docx_path: Path) -> StyleFingerprint:
    docx_path = Path(docx_path)
    if not docx_path.exists():
        return StyleFingerprint()

    try:
        from docx import Document

        doc = Document(str(docx_path))
        page = {}
        if doc.sections:
            section = doc.sections[0]
            page = {
                "width_pt": _pt(section.page_width),
                "height_pt": _pt(section.page_height),
                "left_pt": _pt(section.left_margin),
                "right_pt": _pt(section.right_margin),
                "top_pt": _pt(section.top_margin),
                "bottom_pt": _pt(section.bottom_margin),
            }

        styles = {}
        for style_name in PRIORITY_STYLES:
            try:
                style = doc.styles[style_name]
            except KeyError:
                continue
            styles[style_name] = _style_definition(style)

        heading_samples = []
        body_samples = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            style_name = para.style.name if para.style else ""
            sample = {
                "style": style_name,
                "text": _truncate(text, 36),
                **_paragraph_effective(para),
            }
            if style_name.lower().startswith("heading") or style_name in {"Title", "Subtitle"}:
                if len(heading_samples) < 8:
                    heading_samples.append(sample)
            elif len(body_samples) < 6:
                body_samples.append(sample)

        has_header, has_footer = _header_footer_flags(docx_path)
        return StyleFingerprint(
            page=page,
            styles=styles,
            has_header=has_header,
            has_footer=has_footer,
            heading_samples=heading_samples,
            body_samples=body_samples,
        )
    except Exception as exc:
        logger.warning("提取样式档案失败: %s", exc, exc_info=True)
        return StyleFingerprint()


def _style_definition(style) -> Dict[str, Any]:
    attrs: Dict[str, Any] = {}
    try:
        font = style.font
        if font.name:
            attrs["ascii"] = font.name
        if font.size:
            attrs["size_pt"] = _pt(font.size)
        if font.bold is not None:
            attrs["bold"] = bool(font.bold)
    except Exception:
        pass

    try:
        pf = style.paragraph_format
        if pf.alignment is not None:
            attrs["alignment"] = str(pf.alignment).split(".")[-1]
        if pf.line_spacing is not None:
            attrs["line_spacing"] = (
                _pt(pf.line_spacing) if hasattr(pf.line_spacing, "pt") else float(pf.line_spacing)
            )
        if pf.first_line_indent is not None:
            attrs["first_indent_pt"] = _pt(pf.first_line_indent)
    except Exception:
        pass

    xml_fonts = _rfonts_from_element(getattr(style, "element", None))
    if xml_fonts.get("eastAsia"):
        attrs["eastAsia"] = xml_fonts["eastAsia"]
    if xml_fonts.get("ascii") and not attrs.get("ascii"):
        attrs["ascii"] = xml_fonts["ascii"]
    return attrs


def _paragraph_effective(paragraph) -> Dict[str, Any]:
    attrs: Dict[str, Any] = {}
    try:
        pf = paragraph.paragraph_format
        if pf.alignment is not None:
            attrs["alignment"] = str(pf.alignment).split(".")[-1]
        if pf.line_spacing is not None:
            attrs["line_spacing"] = (
                _pt(pf.line_spacing) if hasattr(pf.line_spacing, "pt") else float(pf.line_spacing)
            )
        if pf.first_line_indent is not None:
            attrs["first_indent_pt"] = _pt(pf.first_line_indent)
    except Exception:
        pass

    for run in paragraph.runs:
        if not (run.text or "").strip():
            continue
        if run.font.name:
            attrs.setdefault("ascii", run.font.name)
        if run.font.size:
            attrs.setdefault("size_pt", _pt(run.font.size))
        if run.font.bold is not None:
            attrs.setdefault("bold", bool(run.font.bold))
        xml_fonts = _rfonts_from_element(run._element)
        if xml_fonts.get("eastAsia"):
            attrs.setdefault("eastAsia", xml_fonts["eastAsia"])
        break

    style_fonts = _rfonts_from_element(getattr(paragraph.style, "element", None)) if paragraph.style else {}
    if style_fonts.get("eastAsia"):
        attrs.setdefault("eastAsia", style_fonts["eastAsia"])
    if style_fonts.get("ascii"):
        attrs.setdefault("ascii", style_fonts["ascii"])
    return attrs


def _rfonts_from_element(element) -> Dict[str, str]:
    if element is None:
        return {}
    rfonts = element.find(f".//{{{W_NS}}}rFonts")
    if rfonts is None:
        return {}
    result = {}
    for key in ("ascii", "eastAsia", "hAnsi", "cs"):
        value = rfonts.get(f"{{{W_NS}}}{key}")
        if value:
            result[key] = value
    return result


def _header_footer_flags(docx_path: Path) -> tuple[bool, bool]:
    try:
        with zipfile.ZipFile(docx_path) as archive:
            names = archive.namelist()
            has_header = any(name.startswith("word/header") for name in names)
            has_footer = any(name.startswith("word/footer") for name in names)
            if not has_header or not has_footer:
                document = ET.fromstring(archive.read("word/document.xml"))
                if document.find(".//w:headerReference", NS) is not None:
                    has_header = True
                if document.find(".//w:footerReference", NS) is not None:
                    has_footer = True
            return has_header, has_footer
    except Exception:
        return False, False


def _pt(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value.pt), 2)
    except Exception:
        try:
            return round(float(value) / 12700.0, 2)
        except Exception:
            return None


def _format_attrs(attrs: Dict[str, Any]) -> str:
    parts = []
    mapping = (
        ("eastAsia", "中文"),
        ("ascii", "西文"),
        ("size_pt", "字号"),
        ("bold", "加粗"),
        ("alignment", "对齐"),
        ("line_spacing", "行距"),
        ("first_indent_pt", "首行缩进"),
    )
    for key, label in mapping:
        if key in attrs and attrs[key] not in (None, ""):
            suffix = "pt" if key.endswith("_pt") else ""
            parts.append(f"{label}={attrs[key]}{suffix}")
    return ", ".join(parts) or "（沿用主题默认值）"


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
