from pathlib import Path
import asyncio
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.core_agents.review_agent import ReviewAgent
from ai_system.core_tools.file_tools import FileTools
from services.file_services.plan_reconciler import PLAN_PHASES, PlanReconciler
from services.file_services.workspace_files import WorkspaceFileService


SAMPLE_PLAN_MD = """# 圆周率实验计划

| 序号 | 章节/任务 | 状态 | 说明 |
|------|-----------|------|------|
| 1 | 明确需求与约束 | ✅ 已完成 | 输出 Markdown 论文 |
| 2 | 论文结构设计 | ✅ 已完成 | 摘要/方法/结果 |
| 3 | 任务拆解 | ⏳ 进行中 | 拆成可执行章节 |
| 4 | 引言 | ⬜ 待写 | 研究背景 |
| 5 | 最终检查与完善 | ⬜ 待写 | 对照需求验收 |
"""


def _write_paper(workspace: Path, text: str = "正文内容") -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "paper.md").write_text("# 圆周率估计\n\n" + text * 40, encoding="utf-8")


def _allow_workspace(monkeypatch, workspace: Path) -> None:
    monkeypatch.setattr(
        "ai_system.core_agents.review_agent.get_workspaces_path",
        lambda: workspace.parent,
    )


def test_plan_json_contract_from_markdown_table(tmp_path: Path):
    workspace = tmp_path / "workspace"
    _write_paper(workspace)
    structured = PlanReconciler(workspace).build_from_markdown(SAMPLE_PLAN_MD)

    assert structured["version"] == 1
    assert structured["methodology"] == "spec-driven"
    assert structured["planning_mode"] == "dynamic"
    assert structured["title"] == "圆周率实验计划"
    assert structured["source"] == "update_plan_markdown"
    assert structured["source_markdown"] == SAMPLE_PLAN_MD
    assert [phase["id"] for phase in structured["phases"]] == [phase["id"] for phase in PLAN_PHASES]
    assert {item["id"] for item in structured["items"]} == {"task-1", "task-2", "task-3", "task-4", "task-5"}
    assert structured["items"][0]["depends_on"] == []
    assert structured["items"][1]["depends_on"] == ["task-1"]
    assert structured["current_focus"]["id"] == "task-3"
    assert [action["title"] for action in structured["next_actions"]] == ["引言", "最终检查与完善"]
    assert structured["stats"]["total"] == 5
    assert structured["stats"]["completed"] == 2
    assert structured["stats"]["in_progress"] == 1
    assert structured["stats"]["pending"] == 2
    assert structured["stats"]["progress_percent"] == 40


def test_plan_items_map_to_spec_driven_phases(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    structured = PlanReconciler(workspace).build_from_markdown(SAMPLE_PLAN_MD)
    phases = {item["title"]: item["phase"] for item in structured["items"]}

    assert phases["明确需求与约束"] == "requirements"
    assert phases["论文结构设计"] == "design"
    assert phases["任务拆解"] == "tasks"
    assert phases["引言"] == "implement"
    assert phases["最终检查与完善"] == "verify"
    assert structured["active_phase"] == "tasks"

    phase_status = {phase["id"]: phase["status"] for phase in structured["phases"]}
    assert phase_status["requirements"] == "completed"
    assert phase_status["design"] == "completed"
    assert phase_status["tasks"] == "in_progress"
    assert phase_status["implement"] == "pending"
    assert phase_status["verify"] == "pending"


def test_active_phase_follows_in_progress_writing_item(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_md = (
        "| 序号 | 章节 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 明确需求与约束 | ✅ 已完成 | 已确认 |\n"
        "| 2 | 引言 | ⏳ 进行中 | 正在写 |\n"
        "| 3 | 最终检查与完善 | ⬜ 待写 | 验收 |\n"
    )
    structured = PlanReconciler(workspace).build_from_markdown(plan_md)

    assert structured["active_phase"] == "implement"
    assert structured["current_focus"]["title"] == "引言"
    phase_status = {phase["id"]: phase["status"] for phase in structured["phases"]}
    assert phase_status["requirements"] == "completed"
    assert phase_status["design"] == "completed"
    assert phase_status["tasks"] == "completed"
    assert phase_status["implement"] == "in_progress"


def test_update_plan_writes_both_files_and_increments_revision(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE_DIR", str(workspace))
    tools = FileTools()

    first = tools.update_plan(SAMPLE_PLAN_MD)
    assert "plan.md / plan.json" in first
    first_plan = json.loads((workspace / "plan.json").read_text(encoding="utf-8"))
    assert first_plan["revision"] == 1
    assert (workspace / "plan.md").exists()

    updated = SAMPLE_PLAN_MD.replace("⏳ 进行中", "✅ 已完成").replace("引言 | ⬜ 待写", "引言 | ⏳ 进行中")
    tools.update_plan(updated)
    second_plan = json.loads((workspace / "plan.json").read_text(encoding="utf-8"))

    assert second_plan["revision"] == 2
    assert second_plan["current_focus"]["title"] == "引言"
    assert second_plan["items"][2]["status"] == "completed"


def test_ensure_plan_json_does_not_bump_revision_when_stable(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "plan.md").write_text(SAMPLE_PLAN_MD, encoding="utf-8")
    reconciler = PlanReconciler(workspace)

    first = reconciler.ensure_plan_json(sync_markdown=True)
    second = reconciler.ensure_plan_json(sync_markdown=True)

    assert first["revision"] == second["revision"]
    assert first["stats"] == second["stats"]


def test_workspace_read_file_prefers_reconciled_plan_json(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "plan.md").write_text(SAMPLE_PLAN_MD, encoding="utf-8")

    service = WorkspaceFileService()
    monkeypatch.setattr(service, "get_workspace_path", lambda work_id: workspace)

    result = service.read_file("w1", "plan.json")
    data = json.loads(result["content"])

    assert result["filename"] == "plan.json"
    assert data["version"] == 1
    assert data["items"][0]["id"] == "task-1"
    assert (workspace / "plan.json").exists()


def test_review_agent_prefers_plan_json_over_stale_markdown(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    _write_paper(workspace)
    _allow_workspace(monkeypatch, workspace)
    (workspace / "plan.md").write_text(
        "| 序号 | 章节 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 引言 | ⬜ 待写 | 旧状态 |\n",
        encoding="utf-8",
    )
    (workspace / "plan.json").write_text(
        json.dumps(
            {
                "version": 1,
                "revision": 4,
                "title": "写作计划",
                "items": [
                    {
                        "id": "task-1",
                        "order": 1,
                        "title": "引言",
                        "status": "completed",
                        "phase": "implement",
                    }
                ],
                "stats": {
                    "total": 1,
                    "completed": 1,
                    "in_progress": 0,
                    "blocked": 0,
                    "pending": 0,
                    "progress_percent": 100,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reviewer = ReviewAgent(llm=None, workspace_dir=str(workspace), output_mode="markdown")
    blockers = reviewer._deterministic_blockers(
        (workspace / "plan.md").read_text(encoding="utf-8"),
        "paper.md: 1200 字符",
        reviewer._load_structured_plan(),
    )

    assert blockers == []


def test_review_agent_uses_plan_json_when_markdown_is_missing(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _allow_workspace(monkeypatch, workspace)
    (workspace / "paper.md").write_text("# 标题\n\n" + "正文" * 40, encoding="utf-8")
    (workspace / "plan.json").write_text(
        json.dumps(
            {
                "items": [
                    {"id": "task-1", "title": "引言", "status": "pending", "phase": "implement"}
                ]
            }
        ),
        encoding="utf-8",
    )

    reviewer = ReviewAgent(llm=None, workspace_dir=str(workspace), output_mode="markdown")
    result = asyncio.run(reviewer.review("写一篇论文"))

    assert result.complete is False
    assert "plan.md 不存在" not in result.reason
    assert "待写或进行中" in result.reason


def test_review_agent_falls_back_to_plan_md_for_old_workspaces(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _allow_workspace(monkeypatch, workspace)
    (workspace / "paper.md").write_text("# 标题\n\n" + "正文" * 40, encoding="utf-8")
    (workspace / "plan.md").write_text(
        "| 序号 | 章节 | 状态 | 说明 |\n"
        "|---|---|---|---|\n"
        "| 1 | 引言 | ❌ 阻塞 | 缺图 |\n",
        encoding="utf-8",
    )

    reviewer = ReviewAgent(llm=None, workspace_dir=str(workspace), output_mode="markdown")
    result = asyncio.run(reviewer.review("写一篇论文"))

    assert result.complete is False
    assert reviewer._load_structured_plan() is None
    assert "阻塞条目" in result.reason


def test_load_structured_plan_rejects_workspace_outside_allow_root(tmp_path: Path, monkeypatch):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "plan.json").write_text(
        json.dumps({"items": [{"id": "task-1", "title": "引言", "status": "pending"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "ai_system.core_agents.review_agent.get_workspaces_path",
        lambda: allowed,
    )
    reviewer = ReviewAgent(llm=None, workspace_dir=str(outside), output_mode="markdown")
    assert reviewer._load_structured_plan() is None


def test_markdown_paper_status_includes_structured_plan(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    _write_paper(workspace)
    (workspace / "plan.md").write_text(SAMPLE_PLAN_MD, encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(workspace))

    status = FileTools().get_paper_status()

    assert "paper.md 写作状态" in status
    assert "计划进度:" in status
    assert "任务拆解" in status
    assert "流程阶段: tasks" in status


def test_initial_workspace_plan_json_uses_requirements_phase(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reconciler = PlanReconciler(workspace)
    content = reconciler.append_template_constraints("# 写作计划\n\n等待AI分析需求并制定写作计划...\n")
    structured = reconciler.build_from_markdown(content, source="initial", revision=0)

    assert structured["active_phase"] == "requirements"
    assert structured["items"][0]["phase"] == "requirements"
    assert structured["items"][0]["status"] == "pending"
