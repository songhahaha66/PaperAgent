"""Progress must survive recovery and follow actual dependency/acceptance changes."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph.context import GraphContext
from ai_system.research.control import public_snapshot
from ai_system.research.models import IssueReport
from ai_system.research.store import ResearchStore
from services.chat_services.research_session import research_snapshot
from test_research_control import make_store, finish


def tasks(store, status=None):
    return {t["task_id"]: t for t in public_snapshot(store.snapshot(), work_status=status)["experiments"]}


def test_dependency_progress_and_recovery_preserve_unrelated_acceptance(tmp_path):
    store = make_store(tmp_path)
    assert tasks(store)["Q1"]["progress"]["state"] == "ready"
    assert tasks(store)["Q2"]["progress"]["waiting_for"] == ["Q1"]
    finish(store, "Q3")
    store.claim("Q1")
    assert tasks(store)["Q1"]["progress"]["state"] == "running"
    assert tasks(store, "paused")["Q1"]["progress"]["state"] == "interrupted"
    store.recover()
    recovered = tasks(ResearchStore(tmp_path, "research"))
    assert recovered["Q3"]["progress"]["state"] == "accepted"
    assert recovered["Q1"]["progress"]["state"] == "rework"
    assert recovered["Q2"]["progress"]["state"] == "waiting"
    assert recovered["Q1"]["progress"]["executions"] == 1
    finish(store, "Q1")
    assert tasks(store)["Q2"]["progress"]["state"] == "ready", "a task never executed is not rework"




def test_open_review_does_not_count_as_accepted_and_budget_is_explicit(tmp_path):
    store = make_store(tmp_path)
    finish(store, "Q1")
    [issue] = store.report_issues([IssueReport(task_id="Q1", category="model", message="scope uncertain")], "sections")
    assert tasks(store)["Q1"]["progress"]["state"] == "review"
    assert tasks(store)["Q1"]["progress"]["issue_ids"] == [issue["id"]]
    for _ in range(3):
        order = store.claim("Q3")
        finish(store, "Q3", acceptance="rejected", order=order)
        store.recover()
    assert tasks(store)["Q3"]["progress"]["state"] == "needs_plan"
    assert tasks(store)["Q3"]["progress"]["executions"] == 3


def test_delivery_progress_is_persisted_projected_and_read_only(tmp_path):
    store = make_store(tmp_path)
    for key in ("Q1", "Q2", "Q3"):
        finish(store, key)
    (tmp_path / ".system/graph_thread.json").write_text(json.dumps({"thread_id": "research", "engine_version": "research-v3", "status": "running"}))
    events = []
    class Stream:
        async def send_json_block(self, kind, content): events.append((kind, content))
    ctx = GraphContext.create(str(tmp_path), planner_llm=None, stream_manager=Stream())
    ctx.research = store
    asyncio.run(ctx.set_stage("write", "in_progress", "writing accepted results"))
    assert any(kind == "research_state" and content["delivery_stages"]["write"]["status"] == "in_progress" for kind, content in events)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    snapshot = research_snapshot(tmp_path, "work", "paused")
    assert all(t["progress"]["state"] == "accepted" for t in snapshot.tasks)
    assert snapshot.delivery_stages["write"]["status"] == "interrupted"
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    store.recover()
    assert public_snapshot(store.snapshot())["delivery_stages"]["write"]["status"] == "pending"
    assert not (tmp_path / "plan.json").exists()


def test_paper_rewrite_does_not_revoke_accepted_computation_progress(tmp_path):
    store = make_store(tmp_path)
    for key in ("Q1", "Q2", "Q3"):
        finish(store, key)
    store.report_issues([IssueReport(task_id="Q1", category="writing", section="analysis", message="rewrite unsupported wording")], "sections")
    progress = public_snapshot(store.snapshot())
    assert not progress["delivery"]["ready"]
    assert all(t["progress"]["state"] == "accepted" for t in progress["experiments"])
    assert all(not t["progress"]["issue_ids"] for t in progress["experiments"])
    store.recover()
    assert all(t["progress"]["state"] == "accepted" for t in tasks(store).values())
