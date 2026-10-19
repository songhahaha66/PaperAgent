from pathlib import Path

import pytest
import sys
import importlib.util
import importlib
from unittest.mock import Mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document

from services.file_services.template_contract import (
    CONTRACT_PATH,
    CONTRACT_FILENAME,
    META_FILENAME,
    STYLE_PROFILE_FILENAME,
    _build_contract,
    _copy_template_to_workspace,
    _prepare_template_workspace,
    analyze_and_store_template,
    apply_stored_template_analysis,
    delete_template_analysis,
    ensure_template_analysis,
    read_template_analysis,
)


def _load_work_routes_module():
    return importlib.import_module("routers.work_routes.work")


def test_markdown_contract_extracts_headings_and_format_requirements(tmp_path: Path):
    template = tmp_path / "template.md"
    template.write_text(
        "# 论文题目\n\n"
        "要求：标题宋体小三居中。\n\n"
        "## 摘要\n\n"
        "## 1. 引言\n",
        encoding="utf-8",
    )

    contract = _build_contract(template, "测试模板", "markdown")

    assert "L1 第1行: 论文题目" in contract
    assert "L2 第5行: 摘要" in contract
    assert "宋体小三居中" in contract
    assert "必须完整保留模板骨架" in contract


def test_word_template_is_copied_as_initial_paper_docx(tmp_path: Path):
    template = tmp_path / "template.docx"
    doc = Document()
    doc.add_paragraph("标题要求：宋体，小三，居中")
    doc.add_paragraph("摘要")
    doc.save(str(template))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    _copy_template_to_workspace(template, workspace, "word")
    contract = _build_contract(template, "Word模板", "word")

    assert (workspace / "paper.docx").exists()
    assert (workspace / "paper.docx").read_bytes() == template.read_bytes()
    assert "标题要求" in contract
    assert "宋体" in contract
    assert "小三" in contract


def test_analyze_and_store_markdown_template_persists_contract(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "services.file_services.template_contract.get_templates_path",
        lambda: tmp_path,
    )
    source = tmp_path / "course.md"
    source.write_text("# 题目\n\n要求：宋体小三居中。\n\n## 摘要\n", encoding="utf-8")

    contract = analyze_and_store_template(source, 7, "实验模板", "markdown")

    assert "宋体小三居中" in contract
    analysis = read_template_analysis(7)
    assert analysis["status"] == "ready"
    assert (tmp_path / ".analysis" / "7" / CONTRACT_FILENAME).exists()
    assert (tmp_path / ".analysis" / "7" / META_FILENAME).exists()


def test_prepare_workspace_reuses_upload_time_analysis(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "services.file_services.template_contract.get_templates_path",
        lambda: tmp_path,
    )
    source = tmp_path / "course.md"
    source.write_text("# 题目\n\n要求：宋体小三居中。\n", encoding="utf-8")
    analyze_and_store_template(source, 11, "实验模板", "markdown")
    source.write_text("# 被改掉的模板\n", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract = _prepare_template_workspace(source, workspace, 11, "实验模板", "markdown")

    assert "宋体小三居中" in contract
    assert "被改掉的模板" not in (workspace / CONTRACT_PATH).read_text(encoding="utf-8")
    assert (workspace / "paper.md").exists()


def test_prepare_workspace_reuses_word_style_profile(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "services.file_services.template_contract.get_templates_path",
        lambda: tmp_path,
    )
    source = tmp_path / "course.docx"
    doc = Document()
    doc.add_paragraph("标题要求：宋体，小三，居中")
    doc.save(str(source))
    analyze_and_store_template(source, 12, "Word模板", "word")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    applied = apply_stored_template_analysis(12, workspace, "word")
    _copy_template_to_workspace(source, workspace, "word")

    assert applied is True
    assert (tmp_path / ".analysis" / "12" / STYLE_PROFILE_FILENAME).exists()
    assert (workspace / CONTRACT_PATH).exists()
    assert "宋体" in (workspace / CONTRACT_PATH).read_text(encoding="utf-8")
    assert (workspace / "paper.docx").exists()


def test_delete_template_analysis_removes_sidecar_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "services.file_services.template_contract.get_templates_path",
        lambda: tmp_path,
    )
    source = tmp_path / "course.md"
    source.write_text("# 题目\n", encoding="utf-8")
    analyze_and_store_template(source, 13, "实验模板", "markdown")
    assert (tmp_path / ".analysis" / "13").exists()

    delete_template_analysis(13)

    assert not (tmp_path / ".analysis" / "13").exists()


def test_ensure_template_analysis_backfills_old_template(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "services.file_services.template_contract.get_templates_path",
        lambda: tmp_path,
    )
    source = tmp_path / "old.md"
    source.write_text("# 题目\n\n要求：宋体小三居中。\n", encoding="utf-8")
    template = SimpleNamespace(id=21, file_path="old.md", name="旧模板", output_format="markdown")

    analysis = ensure_template_analysis(template)

    assert analysis["status"] == "ready"
    assert "宋体小三居中" in analysis["contract"]
    assert (tmp_path / ".analysis" / "21" / CONTRACT_FILENAME).exists()
