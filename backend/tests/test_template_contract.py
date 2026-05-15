from pathlib import Path
import sys
import asyncio
import importlib.util
import importlib
import subprocess
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document
from docx.enum.style import WD_STYLE_TYPE

from services.file_services.template_contract import (
    CONTRACT_PATH,
    _build_contract,
    _copy_template_to_workspace,
)
from services.file_services.plan_reconciler import PlanReconciler
from ai_system.core_tools.file_tools import FileTools
from ai_system.core_tools.docx_tools import DocxTools


def _load_review_agent_class():
    module_path = Path(__file__).resolve().parents[1] / "ai_system/core_agents/review_agent.py"
    spec = importlib.util.spec_from_file_location("review_agent_for_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.ReviewAgent


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


def test_write_to_template_skips_toc_and_preserves_heading(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    doc = Document()
    doc.styles.add_style("TOC 1", WD_STYLE_TYPE.PARAGRAPH)
    doc.add_paragraph("1.2.1 创建数据表", style="TOC 1")
    doc.add_paragraph("1.2.1 创建数据表", style="Heading 4")
    doc.add_paragraph("执行代码：")
    doc.save(str(workspace / "paper.docx"))

    tools = DocxTools(str(workspace))
    result = asyncio.run(
        tools.write_to_template(
            anchor_text="1.2.1 创建数据表",
            content="这里是实验说明。",
            position="after",
        )
    )

    assert "Error" not in result
    updated = Document(str(workspace / "paper.docx"))
    paragraphs = [p.text for p in updated.paragraphs]
    assert paragraphs[:4] == [
        "1.2.1 创建数据表",
        "1.2.1 创建数据表",
        "这里是实验说明。",
        "执行代码：",
    ]


def test_write_to_template_rejects_heading_replacement(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    doc = Document()
    doc.add_paragraph("1.2.1 创建数据表", style="Heading 4")
    doc.save(str(workspace / "paper.docx"))

    tools = DocxTools(str(workspace))
    result = asyncio.run(
        tools.write_to_template(
            anchor_text="1.2.1 创建数据表",
            content="错误替换标题",
            position="replace",
        )
    )

    assert "禁止替换模板标题段落" in result
    updated = Document(str(workspace / "paper.docx"))
    assert updated.paragraphs[0].text == "1.2.1 创建数据表"


def test_repair_template_structure_restores_heading_text(tmp_path: Path):
    workspace = tmp_path / "workspace"
    system_dir = workspace / ".system"
    system_dir.mkdir(parents=True)

    template = Document()
    template.add_paragraph("第一章 DDL", style="Heading 1")
    template.add_paragraph("1.2.1 创建数据表", style="Heading 4")
    template.add_paragraph("执行代码：")
    template.save(str(system_dir / "_template_original.docx"))

    paper = Document()
    paper.add_paragraph("第一章 DDL", style="Heading 1")
    paper.add_paragraph("**写在前面**：错误替换了标题", style="Heading 4")
    paper.add_paragraph("正文内容应保留")
    paper.save(str(workspace / "paper.docx"))

    tools = DocxTools(str(workspace))
    result = asyncio.run(tools.repair_template_structure())

    assert "已按模板恢复标题骨架" in result
    updated = Document(str(workspace / "paper.docx"))
    assert [p.text for p in updated.paragraphs] == [
        "第一章 DDL",
        "1.2.1 创建数据表",
        "正文内容应保留",
    ]
    (workspace / "plan.md").write_text(
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 最终检查与完善 | ✅ 已完成 | 已验收 |\n",
        encoding="utf-8",
    )
    structured = PlanReconciler(workspace).build_from_markdown((workspace / "plan.md").read_text(encoding="utf-8"))
    assert structured["evidence"]["docx_template_issues"] == []


def test_review_agent_blocks_word_template_heading_drift(tmp_path: Path):
    workspace = tmp_path / "workspace"
    system_dir = workspace / ".system"
    system_dir.mkdir(parents=True)

    template = Document()
    template.add_paragraph("第一章 DDL", style="Heading 1")
    template.add_paragraph("1.2.1 创建数据表", style="Heading 4")
    template.add_paragraph("执行代码：")
    template.save(str(system_dir / "_template_original.docx"))

    paper = Document()
    paper.add_paragraph("第一章 DDL", style="Heading 1")
    paper.add_paragraph("**写在前面**：错误替换了标题", style="Heading 4")
    paper.add_paragraph("执行代码：")
    paper.save(str(workspace / "paper.docx"))

    (workspace / "plan.md").write_text(
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 第一章 DDL | ✅ 已完成 | 已写 |\n",
        encoding="utf-8",
    )

    ReviewAgent = _load_review_agent_class()
    reviewer = ReviewAgent(llm=None, workspace_dir=str(workspace), output_mode="word")
    result = asyncio.run(reviewer.review("完成这个实验报告"))

    assert result.complete is False
    assert "Word模板结构验收未通过" in result.reason


def test_review_agent_blocks_plan_blocked_status_without_llm(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    doc = Document()
    doc.add_paragraph("完整文档正文")
    doc.save(str(workspace / "paper.docx"))
    (workspace / "plan.md").write_text(
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 最终检查与完善 | ❌ 阻塞 | 等待修复 |\n",
        encoding="utf-8",
    )

    ReviewAgent = _load_review_agent_class()
    reviewer = ReviewAgent(llm=None, workspace_dir=str(workspace), output_mode="word")
    result = asyncio.run(reviewer.review("完成这个实验报告"))

    assert result.complete is False
    assert "阻塞条目" in result.reason


def test_review_agent_ignores_status_words_in_template_constraints(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    doc = Document()
    doc.add_paragraph("完整文档正文")
    doc.save(str(workspace / "paper.docx"))
    (workspace / "plan.md").write_text(
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 最终检查与完善 | ✅ 已完成 | 已验收 |\n\n"
        "<!-- template-constraints:start -->\n"
        "模板说明：这里可能出现 待写、进行中、阻塞 等普通文字。\n"
        "<!-- template-constraints:end -->\n",
        encoding="utf-8",
    )

    ReviewAgent = _load_review_agent_class()
    statuses = ReviewAgent._extract_plan_statuses((workspace / "plan.md").read_text(encoding="utf-8"))

    assert statuses == ["completed"]
    assert not ReviewAgent(llm=None, workspace_dir=str(workspace), output_mode="word")._deterministic_blockers(
        (workspace / "plan.md").read_text(encoding="utf-8"),
        "paper.docx: 1000 字节\nWord结构验收: 未发现模板结构问题",
    )


def test_review_agent_blocks_missing_plan_or_paper_without_llm(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    ReviewAgent = _load_review_agent_class()
    reviewer = ReviewAgent(llm=None, workspace_dir=str(workspace), output_mode="word")
    result = asyncio.run(reviewer.review("完成这个实验报告"))

    assert result.complete is False
    assert "plan.md 不存在" in result.reason
    assert "paper.docx 不存在" in result.reason


def test_review_agent_blocks_unparseable_plan_without_llm(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    doc = Document()
    doc.add_paragraph("完整文档正文")
    doc.save(str(workspace / "paper.docx"))
    (workspace / "plan.md").write_text("# 写作计划\n\n等待AI分析需求并制定写作计划...\n", encoding="utf-8")

    ReviewAgent = _load_review_agent_class()
    reviewer = ReviewAgent(llm=None, workspace_dir=str(workspace), output_mode="word")
    result = asyncio.run(reviewer.review("完成这个实验报告"))

    assert result.complete is False
    assert "未包含可解析的计划状态表" in result.reason


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


def test_get_paper_status_reports_word_template_issues(tmp_path: Path, monkeypatch):
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
    (workspace / "plan.md").write_text(
        "| 序号 | 章节名 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 最终检查与完善 | ❌ 阻塞 | 等待修复 |\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("WORKSPACE_DIR", str(workspace))
    status = FileTools().get_paper_status()

    assert "paper.docx 写作状态" in status
    assert "Word模板结构验收问题" in status
    assert "未完成/阻塞任务" in status


def test_ai_system_direct_import_uses_backend_config(tmp_path: Path):
    repo_backend = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                f"os.environ['WORKSPACE_DIR'] = {str(tmp_path)!r}; "
                "from ai_system.core_tools.file_tools import FileTools; "
                "from config.paths import get_templates_path; "
                "print(FileTools.__name__, callable(get_templates_path))"
            ),
        ],
        cwd=str(repo_backend),
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stderr
    assert "FileTools True" in proc.stdout


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
