from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document

from services.file_services.template_contract import (
    CONTRACT_PATH,
    _build_contract,
    _copy_template_to_workspace,
)
from services.file_services.plan_reconciler import PlanReconciler


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


def test_plan_system_loads_template_contract_into_plan_md_and_json(tmp_path: Path):
    workspace = tmp_path / "workspace"
    contract_path = workspace / CONTRACT_PATH
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text("# 模板契约\n\n- 标题必须宋体小三居中\n", encoding="utf-8")

    reconciler = PlanReconciler(workspace)
    plan_md = reconciler.append_template_constraints("# 写作计划\n\n等待AI分析需求并制定写作计划...\n")
    structured = reconciler.build_from_markdown(plan_md)

    assert "## 模板强制约束" in plan_md
    assert "宋体小三居中" in plan_md
    assert structured["constraints"]["template_contract"].startswith("# 模板契约")
    assert structured["constraints"]["plan_markdown_synced"] is True
