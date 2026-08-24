"""
Word style fingerprint and before/after comparison.

Typesetting needs two observations of the same document family:
1. Template: page setup, named styles, header/footer, heading/body samples.
2. Finished paper: the same fingerprint, then a deterministic diff.

Vision can comment on rendered look, but XML style drift is the hard gate.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET
import zipfile

logger = logging.getLogger(__name__)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}

PROFILE_PATH = ".system/template_style_profile.json"
PRIORITY_STYLES = (
    "Normal",
    "Title",
    "Subtitle",
    "Heading 1",
    "Heading 2",
    "Heading 3",
    "Heading 4",
)
PT_TOLERANCE = 1.6

# Template headings often include “成稿后删除此括号” instructions.
# Finished papers are supposed to drop those parentheses; keep the core title.
_DELETABLE_HEADING_PAREN = re.compile(
    r"[（(][^（）()]{0,120}成稿后删除[^（）()]{0,120}[）)]\s*$"
)


def canonical_heading_text(text: str) -> str:
    return _DELETABLE_HEADING_PAREN.sub("", (text or "").strip()).strip()


def heading_outlines_equivalent(
    expected: Sequence[Tuple[str, str]],
    actual: Sequence[Tuple[str, str]],
) -> bool:
    return not heading_outline_issues(expected, actual)


def heading_outline_issues(
    expected: Sequence[Tuple[str, str]],
    actual: Sequence[Tuple[str, str]],
) -> List[str]:
    issues: List[str] = []
    for idx, ((expected_style, expected_text), (actual_style, actual_text)) in enumerate(
        zip(expected, actual),
        start=1,
    ):
        if expected_style != actual_style or canonical_heading_text(expected_text) != canonical_heading_text(
            actual_text
        ):
            issues.append("标题层级/顺序/文本与模板不一致")
            issues.append(f"第 {idx} 个标题应为 {expected_text}，当前为 {actual_text}")
            break
    if len(expected) != len(actual):
        if not issues:
            issues.append("标题层级/顺序/文本与模板不一致")
        issues.append(f"标题数量不一致：模板 {len(expected)} 个，当前 {len(actual)} 个")
    return issues


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


def compare_style_fingerprints(
    expected: StyleFingerprint,
    actual: StyleFingerprint,
) -> List[str]:
    issues: List[str] = []

    for key, label in (
        ("width_pt", "页面宽度"),
        ("height_pt", "页面高度"),
        ("left_pt", "左边距"),
        ("right_pt", "右边距"),
        ("top_pt", "上边距"),
        ("bottom_pt", "下边距"),
    ):
        exp = expected.page.get(key)
        act = actual.page.get(key)
        if exp is None or act is None:
            continue
        if abs(float(exp) - float(act)) > PT_TOLERANCE:
            issues.append(f"{label}不一致：模板 {exp}pt，当前 {act}pt")

    if expected.has_header and not actual.has_header:
        issues.append("成品丢失了模板页眉")
    if expected.has_footer and not actual.has_footer:
        issues.append("成品丢失了模板页脚")

    for name, exp_attrs in expected.styles.items():
        act_attrs = actual.styles.get(name)
        if act_attrs is None:
            issues.append(f"成品缺少样式定义: {name}")
            continue
        for field_name, label in (
            ("eastAsia", "中文字体"),
            ("ascii", "西文字体"),
            ("size_pt", "字号"),
            ("bold", "加粗"),
            ("alignment", "对齐"),
            ("line_spacing", "行距"),
            ("first_indent_pt", "首行缩进"),
        ):
            exp = exp_attrs.get(field_name)
            act = act_attrs.get(field_name)
            if exp in (None, "", "None") or act in (None, "", "None"):
                continue
            if field_name in {"size_pt", "line_spacing", "first_indent_pt"}:
                try:
                    if abs(float(exp) - float(act)) > PT_TOLERANCE:
                        issues.append(f"样式 {name} 的{label}不一致：模板 {exp}，当前 {act}")
                except (TypeError, ValueError):
                    if str(exp) != str(act):
                        issues.append(f"样式 {name} 的{label}不一致：模板 {exp}，当前 {act}")
            elif str(exp) != str(act):
                issues.append(f"样式 {name} 的{label}不一致：模板 {exp}，当前 {act}")

    return issues


def compare_docx_styles(expected_path: Path, actual_path: Path) -> List[str]:
    return compare_style_fingerprints(
        extract_style_fingerprint(expected_path),
        extract_style_fingerprint(actual_path),
    )


def format_style_comparison(
    expected: StyleFingerprint,
    actual: StyleFingerprint,
    issues: Iterable[str],
) -> str:
    issues = list(issues)
    lines = [
        expected.format_report("模板样式").rstrip(),
        "",
        actual.format_report("成品样式").rstrip(),
        "",
        "## 样式对照结果",
    ]
    if issues:
        lines.append(f"- 发现 {len(issues)} 处样式漂移：")
        lines.extend(f"  - {item}" for item in issues)
    else:
        lines.append("- 页面、页眉页脚和关键样式定义与模板一致。")
    return "\n".join(lines) + "\n"


def save_style_profile(workspace_path: Path, fingerprint: StyleFingerprint) -> Path:
    path = Path(workspace_path) / PROFILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(fingerprint.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def infer_body_format(doc, style_name: str = "Normal") -> Dict[str, Any]:
    """
    Resolve the fonts/spacing that new body text should inherit.

    Complex course templates often leave Normal empty and put 宋体/小四
    on real paragraphs. Style definitions alone are not enough.
    """
    attrs: Dict[str, Any] = {}
    try:
        attrs.update(_style_definition(doc.styles[style_name]))
    except KeyError:
        pass

    votes: Dict[str, List[Any]] = {
        "eastAsia": [],
        "ascii": [],
        "size_pt": [],
        "line_spacing": [],
        "first_indent_pt": [],
        "alignment": [],
    }
    for para in getattr(doc, "paragraphs", []):
        style = (para.style.name if para.style else "") or ""
        if style != style_name:
            continue
        text = para.text.strip()
        if not text:
            sample = _paragraph_effective(para)
            if sample.get("line_spacing") not in (None, ""):
                votes["line_spacing"].append(sample["line_spacing"])
            continue
        if text.startswith(("图", "表")):
            continue
        sample = _paragraph_effective(para)
        size_pt = sample.get("size_pt")
        if size_pt is not None:
            try:
                if float(size_pt) >= 15.5:
                    continue
            except (TypeError, ValueError):
                pass
        if sample.get("bold") and size_pt:
            try:
                if float(size_pt) >= 15:
                    continue
            except (TypeError, ValueError):
                pass
        for key in votes:
            value = sample.get(key)
            if value not in (None, "", "None"):
                votes[key].append(value)

    for key, values in votes.items():
        if not values:
            continue
        chosen = _majority(values)
        if attrs.get(key) in (None, "", "None"):
            attrs[key] = chosen
        elif key in {"eastAsia", "ascii", "size_pt"}:
            attrs[key] = chosen
    return attrs


def apply_body_paragraph_format(doc, paragraph, *, style_name: str = "Normal") -> None:
    """Copy inferred body spacing/alignment onto a newly inserted paragraph."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    attrs = infer_body_format(doc, style_name=style_name)
    alignment = str(attrs.get("alignment") or "")
    if alignment in {"JUSTIFY", "3"}:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    elif alignment in {"CENTER", "1"}:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif alignment in {"RIGHT", "2"}:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif alignment in {"LEFT", "0"}:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT

    if attrs.get("line_spacing") not in (None, ""):
        try:
            paragraph.paragraph_format.line_spacing = float(attrs["line_spacing"])
        except (TypeError, ValueError):
            pass
    if attrs.get("first_indent_pt") not in (None, ""):
        try:
            paragraph.paragraph_format.first_line_indent = Pt(float(attrs["first_indent_pt"]))
        except (TypeError, ValueError):
            pass


def apply_body_run_format(doc, run, *, style_name: str = "Normal", is_code: bool = False) -> None:
    """Copy template body fonts onto a newly inserted run."""
    from docx.oxml.ns import qn
    from docx.shared import Pt

    if is_code:
        run.font.name = "Courier New"
        run.font.size = Pt(10)
        try:
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "Courier New")
        except Exception:
            pass
        return

    attrs = infer_body_format(doc, style_name=style_name)
    if attrs.get("ascii"):
        run.font.name = attrs["ascii"]
    if attrs.get("size_pt"):
        try:
            run.font.size = Pt(float(attrs["size_pt"]))
        except (TypeError, ValueError):
            pass
    if attrs.get("bold") is True:
        run.font.bold = True
    east_asia = attrs.get("eastAsia") or attrs.get("ascii")
    if east_asia:
        try:
            r_pr = run._element.get_or_add_rPr()
            r_fonts = r_pr.get_or_add_rFonts()
            r_fonts.set(qn("w:eastAsia"), east_asia)
            ascii_name = attrs.get("ascii") or east_asia
            r_fonts.set(qn("w:ascii"), ascii_name)
            r_fonts.set(qn("w:hAnsi"), ascii_name)
        except Exception:
            logger.debug("写入 eastAsia 字体失败", exc_info=True)


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


def _majority(values: List[Any]) -> Any:
    counts: Dict[str, int] = {}
    keyed: Dict[str, Any] = {}
    for value in values:
        if isinstance(value, float):
            key = f"{round(value, 2)}"
        else:
            key = str(value)
        counts[key] = counts.get(key, 0) + 1
        keyed.setdefault(key, value)
    return keyed[max(counts, key=counts.get)]


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
