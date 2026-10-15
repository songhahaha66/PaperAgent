import importlib.util
import struct
import sys
import zlib
from pathlib import Path

from docx import Document
from docx.shared import Inches

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx.oxml.ns import qn
from docx.shared import Pt

from ai_system.config.api_settings import normalize_openai_base_url
from ai_system.core_tools.docx_images import extract_docx_images, inventory_docx_images
from ai_system.core_tools.docx_styles import (
    compare_style_fingerprints,
    extract_style_fingerprint,
    canonical_heading_text,
    heading_outlines_equivalent,
)


def _load_template_contract():
    module_path = Path(__file__).resolve().parents[1] / "services/file_services/template_contract.py"
    spec = importlib.util.spec_from_file_location("template_contract_for_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


_template_contract = _load_template_contract()
_build_contract = _template_contract._build_contract
_copy_template_to_workspace = _template_contract._copy_template_to_workspace


def _tiny_png(path: Path, color: bytes = b"\xff\x00\x00", width: int = 96, height: int = 64) -> Path:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = (b"\x00" + color * width) * height
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


def _docx_with_image(tmp_path: Path) -> Path:
    image = _tiny_png(tmp_path / "logo.png")
    doc_path = tmp_path / "template.docx"
    doc = Document()
    doc.add_paragraph("实验报告封面", style="Heading 1")
    doc.add_picture(str(image), width=Inches(1.2))
    doc.add_paragraph("1.2 实验结果", style="Heading 2")
    doc.add_paragraph("请在此插入收敛曲线图")
    doc.save(str(doc_path))
    return doc_path


def test_normalize_openai_base_url_appends_v1():
    assert normalize_openai_base_url("http://example.com:8080") == "http://example.com:8080/v1"
    assert normalize_openai_base_url("http://example.com:8080/v1") == "http://example.com:8080/v1"
    assert normalize_openai_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"


def test_inventory_and_extract_docx_images(tmp_path: Path):
    docx_path = _docx_with_image(tmp_path)
    images = inventory_docx_images(docx_path)

    assert len(images) == 1
    assert images[0].filename.lower().endswith((".png", ".jpeg", ".jpg"))
    assert "实验报告封面" in images[0].nearby_text or "实验结果" in images[0].nearby_text

    extracted = extract_docx_images(docx_path, tmp_path / "extracted")
    assert extracted[0].extracted_path
    assert Path(extracted[0].extracted_path).exists()


def test_word_contract_lists_embedded_images(tmp_path: Path):
    docx_path = _docx_with_image(tmp_path)
    contract = _build_contract(docx_path, "带图模板", "word")

    assert "图片骨架" in contract
    assert "模板样式档案" in contract
    assert "嵌入图片" in contract or "张嵌入图片" in contract
    assert "logo" in contract.lower() or "image" in contract.lower()


def test_copy_template_does_not_require_images(tmp_path: Path):
    template = tmp_path / "plain.docx"
    doc = Document()
    doc.add_paragraph("只有文字")
    doc.save(str(template))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _copy_template_to_workspace(template, workspace, "word")
    assert (workspace / "paper.docx").exists()


def _set_east_asia_style(doc: Document, style_name: str, font_name: str, size_pt: float) -> None:
    style = doc.styles[style_name]
    style.font.name = "Times New Roman"
    style.font.size = Pt(size_pt)
    r_pr = style.element.get_or_add_rPr()
    r_fonts = r_pr.get_or_add_rFonts()
    r_fonts.set(qn("w:eastAsia"), font_name)
    r_fonts.set(qn("w:ascii"), "Times New Roman")


def test_style_fingerprint_reads_page_and_cjk_font(tmp_path: Path):
    path = tmp_path / "styled.docx"
    doc = Document()
    _set_east_asia_style(doc, "Normal", "宋体", 12)
    _set_east_asia_style(doc, "Heading 1", "黑体", 16)
    doc.add_paragraph("封面标题", style="Heading 1")
    doc.add_paragraph("这是一段正文")
    doc.save(str(path))

    fingerprint = extract_style_fingerprint(path)
    assert fingerprint.page.get("width_pt")
    assert fingerprint.styles["Normal"]["eastAsia"] == "宋体"
    assert fingerprint.styles["Heading 1"]["eastAsia"] == "黑体"
    assert fingerprint.styles["Normal"]["size_pt"] == 12


def test_style_fingerprints_detect_font_and_margin_drift(tmp_path: Path):
    template = Document()
    _set_east_asia_style(template, "Normal", "宋体", 12)
    template.sections[0].left_margin = Pt(72)
    template.add_paragraph("第一章", style="Heading 1")
    template.save(str(tmp_path / "template.docx"))

    paper = Document()
    _set_east_asia_style(paper, "Normal", "微软雅黑", 10.5)
    paper.sections[0].left_margin = Pt(36)
    paper.add_paragraph("第一章", style="Heading 1")
    paper.save(str(tmp_path / "paper.docx"))

    expected = extract_style_fingerprint(tmp_path / "template.docx")
    actual = extract_style_fingerprint(tmp_path / "paper.docx")
    issues = compare_style_fingerprints(expected, actual)

    assert any("中文字体" in item for item in issues)
    assert any("左边距" in item for item in issues)


def test_canonical_heading_drops_deletable_parentheses():
    original = "2 项目开发报告（以下内容可根据自己的内容，进行替换更改结构，成稿后删除此括号，包含括号内容）"
    assert canonical_heading_text(original) == "2 项目开发报告"
    assert heading_outlines_equivalent(
        [("heading 1", original), ("heading 3", "2.4.2 核心代码（没有代码，可写制作流程，成稿后删除此括号，包含括号内容）")],
        [("heading 1", "2 项目开发报告"), ("heading 3", "2.4.2 核心代码")],
    )
    assert not heading_outlines_equivalent(
        [("heading 1", "2 项目开发报告")],
        [("heading 1", "3 其他章节")],
    )
