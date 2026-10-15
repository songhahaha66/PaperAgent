from pathlib import Path
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
from services.file_services.plan_reconciler import PlanReconciler


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


def test_plan_reconciler_blocks_completion_when_word_template_drifts(tmp_path: Path):
    workspace = tmp_path / "workspace"
    system_dir = workspace / ".system"
    system_dir.mkdir(parents=True)

    template = Document()
    template.add_paragraph("第一章 DDL", style="Heading 1")
    template.add_paragraph("1.2.1 创建数据表", style="Heading 4")
    template.save(str(system_dir / "_template_original.docx"))
    (system_dir / "template_contract.md").write_text("# 模板契约\n", encoding="utf-8")

    paper = Document()
    paper.add_paragraph("第一章 DDL", style="Heading 1")
    paper.add_paragraph("**写在前面**：错误替换了标题", style="Heading 4")
    paper.save(str(workspace / "paper.docx"))

    plan_md = (
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 第一章 DDL | ✅ 已完成 | 已写 |\n"
        "| 2 | 最终检查与完善 | ✅ 已完成 | 检查完成 |\n"
    )
    structured = PlanReconciler(workspace).build_from_markdown(plan_md)

    assert structured["stats"]["blocked"] == 1
    assert structured["stats"]["progress_percent"] < 100
    assert structured["evidence"]["docx_template_issues"]
    final_item = next(item for item in structured["items"] if item["title"] == "最终检查与完善")
    assert final_item["status"] == "blocked"
    assert structured["current_focus"]["title"] == "最终检查与完善"
    assert final_item["raw_status"] == "template_validation_failed"
    assert final_item["phase"] == "verify"


def test_plan_reconciler_syncs_metadata_progress_and_review_status(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "metadata.json").write_text(
        '{"work_id": "w1", "created_at": "2026-05-15", "status": "created", "progress": 0}',
        encoding="utf-8",
    )

    plan_md = (
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 第一章 | ✅ 已完成 | 已写 |\n"
        "| 2 | 第二章 | ⏳ 进行中 | 写作中 |\n"
    )
    reconciler = PlanReconciler(workspace)
    structured = reconciler.build_from_markdown(plan_md)
    reconciler.write_plan_json(structured)

    metadata = __import__("json").loads((workspace / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "running"
    assert metadata["progress"] == 50
    assert metadata["review_status"] == "in_progress"


def test_plan_reconciler_status_normalization_prioritizes_blocked(tmp_path: Path):
    reconciler = PlanReconciler(tmp_path)

    assert reconciler._normalize_plan_status("❌ 阻塞（原状态 ✅ 已完成）") == "blocked"
    assert reconciler._normalize_plan_status("⬜ 待写") == "pending"
    assert reconciler._normalize_plan_status("⏳ 进行中") == "in_progress"
    assert reconciler._normalize_plan_status("✅ 已完成") == "completed"


def test_plan_reconciler_syncs_metadata_even_when_plan_is_stable(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "metadata.json").write_text(
        '{"work_id": "w1", "created_at": "2026-05-15", "status": "created", "progress": 0}',
        encoding="utf-8",
    )
    plan_md = (
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 第一章 | ✅ 已完成 | 已写 |\n"
    )
    (workspace / "plan.md").write_text(plan_md, encoding="utf-8")
    (workspace / "paper.md").write_text("# 第一章\n\n" + "正文内容" * 80, encoding="utf-8")

    reconciler = PlanReconciler(workspace)
    first = reconciler.ensure_plan_json(sync_markdown=True)
    stale_metadata = __import__("json").loads((workspace / "metadata.json").read_text(encoding="utf-8"))
    stale_metadata["status"] = "created"
    stale_metadata["progress"] = 0
    stale_metadata.pop("review_status", None)
    (workspace / "metadata.json").write_text(
        __import__("json").dumps(stale_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    second = reconciler.ensure_plan_json(sync_markdown=True)
    metadata = __import__("json").loads((workspace / "metadata.json").read_text(encoding="utf-8"))

    assert first["stats"] == second["stats"]
    assert metadata["status"] == "completed"
    assert metadata["progress"] == 100
    assert metadata["review_status"] == "passed"


def test_plan_reconciler_does_not_complete_metadata_without_document(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "metadata.json").write_text(
        '{"work_id": "w1", "created_at": "2026-05-15", "status": "created", "progress": 0}',
        encoding="utf-8",
    )
    (workspace / "plan.md").write_text(
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 第一章 | ✅ 已完成 | 已写 |\n",
        encoding="utf-8",
    )

    PlanReconciler(workspace).ensure_plan_json(sync_markdown=True)
    metadata = __import__("json").loads((workspace / "metadata.json").read_text(encoding="utf-8"))

    assert metadata["progress"] == 100
    assert metadata["status"] == "running"
    assert metadata["review_status"] == "blocked"
    assert "论文产物不存在或内容不足" in metadata["review_reason"]


def test_get_work_metadata_reconciles_plan_before_reading(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "metadata.json").write_text(
        '{"work_id": "w1", "created_at": "2026-05-15", "status": "created", "progress": 0}',
        encoding="utf-8",
    )
    (workspace / "plan.md").write_text(
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 第一章 | ✅ 已完成 | 已写 |\n",
        encoding="utf-8",
    )
    (workspace / "paper.md").write_text("# 第一章\n\n" + "正文内容" * 80, encoding="utf-8")

    work_module = _load_work_routes_module()
    monkeypatch.setattr(work_module, "get_workspace_path", lambda work_id: workspace)
    monkeypatch.setattr(work_module.crud, "get_work", lambda db, work_id: Mock(created_by=7))

    metadata = __import__("asyncio").run(
        work_module.get_work_metadata("w1", db=object(), current_user=7)
    )

    assert metadata["status"] == "completed"
    assert metadata["progress"] == 100


def test_get_work_detail_syncs_status_from_workspace_metadata(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "metadata.json").write_text(
        '{"work_id": "w1", "created_at": "2026-05-15", "status": "created", "progress": 0}',
        encoding="utf-8",
    )
    (workspace / "plan.md").write_text(
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 第一章 | ✅ 已完成 | 已写 |\n",
        encoding="utf-8",
    )
    (workspace / "paper.md").write_text("# 第一章\n\n" + "正文内容" * 80, encoding="utf-8")

    work_module = _load_work_routes_module()
    work = Mock(work_id="w1", created_by=7, status="created", progress=0)
    monkeypatch.setattr(work_module, "get_workspace_path", lambda work_id: workspace)
    monkeypatch.setattr(work_module.crud, "get_work", lambda db, work_id: work)

    result = __import__("asyncio").run(work_module.get_work("w1", db=object(), current_user=7))

    assert result.status == "completed"
    assert result.progress == 100


def test_get_works_list_syncs_status_from_workspace_metadata(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "metadata.json").write_text(
        '{"work_id": "w1", "created_at": "2026-05-15", "status": "created", "progress": 0}',
        encoding="utf-8",
    )
    (workspace / "plan.md").write_text(
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 第一章 | ✅ 已完成 | 已写 |\n",
        encoding="utf-8",
    )
    (workspace / "paper.md").write_text("# 第一章\n\n" + "正文内容" * 80, encoding="utf-8")

    work_module = _load_work_routes_module()
    work = Mock(work_id="w1", created_by=7, status="created", progress=0)
    monkeypatch.setattr(work_module, "get_workspace_path", lambda work_id: workspace)
    monkeypatch.setattr(
        work_module.crud,
        "get_user_works",
        lambda db, current_user, skip, limit, status, search: {
            "works": [work],
            "total": 1,
            "page": 1,
            "size": 100,
        },
    )

    result = __import__("asyncio").run(
        work_module.get_works(db=object(), current_user=7)
    )

    assert result["works"][0].status == "completed"
    assert result["works"][0].progress == 100
