import asyncio
import importlib.util
import os
import struct
import sys
import zlib
from pathlib import Path

import pytest
from docx import Document
from docx.shared import Inches

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.config.api_settings import load_env_api_settings, normalize_openai_base_url
from ai_system.core_tools.docx_images import extract_docx_images, inventory_docx_images
from ai_system.core_tools.docx_tools import DocxTools
from ai_system.core_tools.vision_tools import VisionTools


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
    assert normalize_openai_base_url("http://104.249.156.203:30080") == "http://104.249.156.203:30080/v1"
    assert normalize_openai_base_url("http://104.249.156.203:30080/v1") == "http://104.249.156.203:30080/v1"
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


def test_insert_image_to_template_keeps_heading(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    chart = _tiny_png(workspace / "chart.png", color=b"\x00\x80\xff")

    doc = Document()
    doc.add_paragraph("1.2 实验结果", style="Heading 2")
    doc.add_paragraph("请在此插入收敛曲线图")
    doc.save(str(workspace / "paper.docx"))

    tools = DocxTools(str(workspace))
    result = asyncio.run(
        tools.insert_image_to_template(
            image_path="chart.png",
            anchor_text="请在此插入收敛曲线图",
            position="after",
            caption="图1 收敛曲线",
        )
    )

    assert "Error" not in result
    updated = Document(str(workspace / "paper.docx"))
    texts = [p.text for p in updated.paragraphs]
    assert texts[0] == "1.2 实验结果"
    assert "图1 收敛曲线" in texts
    assert inventory_docx_images(workspace / "paper.docx")


def test_insert_image_rejects_heading_replace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    chart = _tiny_png(workspace / "chart.png")
    doc = Document()
    doc.add_paragraph("1.2 实验结果", style="Heading 2")
    doc.save(str(workspace / "paper.docx"))

    tools = DocxTools(str(workspace))
    result = asyncio.run(
        tools.insert_image_to_template(
            image_path=str(chart.name),
            anchor_text="1.2 实验结果",
            position="replace",
        )
    )

    assert "禁止用图片替换模板标题段落" in result
    assert Document(str(workspace / "paper.docx")).paragraphs[0].text == "1.2 实验结果"


def test_get_template_structure_lists_images(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = _docx_with_image(tmp_path)
    (workspace / "paper.docx").write_bytes(source.read_bytes())

    tools = DocxTools(str(workspace))
    structure = asyncio.run(tools.get_template_structure())
    extracted = asyncio.run(tools.extract_template_images())

    assert "图片" in structure
    assert "已提取" in extracted
    assert (workspace / ".system" / "docx_images" / "paper").exists()


def test_analyze_image_without_api_returns_metadata(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    image = _tiny_png(workspace / "cover.png")
    monkeypatch.delenv("PAPERAGENT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    tools = VisionTools(str(workspace))
    result = asyncio.run(tools.analyze_image("cover.png"))

    assert "cover.png" in result
    assert "未配置视觉模型" in result or "识别结果" in result


@pytest.mark.skipif(not os.getenv("PAPERAGENT_API_KEY"), reason="PAPERAGENT_API_KEY 未配置")
def test_live_vision_api_recognizes_color(tmp_path: Path):
    settings = load_env_api_settings()
    assert settings is not None
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _tiny_png(workspace / "blue.png", color=b"\x1f\x4e\x79")

    tools = VisionTools(str(workspace))
    result = asyncio.run(
        tools.analyze_image("blue.png", question="这张图主要是什么颜色？只用一句话中文回答。")
    )

    assert "识别结果" in result
    assert "视觉识别失败" not in result
    assert any(token in result for token in ["蓝", "青", "深蓝"])
