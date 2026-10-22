from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document

from services.file_services.template_contract import CONTRACT_PATH, STYLE_PROFILE_FILENAME, _copy_template_to_workspace, _prepare_template_workspace, analyze_and_store_template, apply_stored_template_analysis


def test_prepare_workspace_invalidates_analysis_when_source_changes(tmp_path: Path, monkeypatch):
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

    assert "被改掉的模板" in contract
    assert "被改掉的模板" in (workspace / CONTRACT_PATH).read_text(encoding="utf-8")
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
    assert (workspace / "paper.docx").read_bytes() == source.read_bytes()
