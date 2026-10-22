"""Progress must survive recovery and follow actual dependency/acceptance changes."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph.context import GraphContext
from ai_system.research.control import public_snapshot
from services.chat_services.research_session import research_snapshot
from helpers.research import make_store, finish


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
    assert all(t["progress"]["state"] == "accepted" for t in snapshot.experiments)
    assert snapshot.delivery_stages["write"]["status"] == "interrupted"
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    store.recover()
    assert public_snapshot(store.snapshot())["delivery_stages"]["write"]["status"] == "pending"
    assert not (tmp_path / "plan.json").exists()
