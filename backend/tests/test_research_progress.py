"""Old verdicts remain readable without controlling scheduling or delivery."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.file_services.workspace_layout import workspace_path
from ai_system.graph.context import GraphContext
from ai_system.research.control import public_snapshot
from services.chat_services.research_session import research_snapshot
from helpers.research import make_store, finish


def test_delivery_progress_is_persisted_projected_and_read_only(tmp_path):
    store = make_store(tmp_path)
    finish(store, 'Q1', acceptance='rejected')
    with store._transaction() as db:
        state=store._read(db)
        state['experiments']['Q1']['acceptance']='rejected'
        state['assumptions']['A1']['status']='refuted'
        state['faults'][0].update(blocking=True,repair_targets={'Q2':1},action='recompute')
        store._save(db,state,'legacy_verdict',{})
    from ai_system.research.planner import ready_tasks
    assert 'Q2' in ready_tasks(store.snapshot())
    for key in ("Q2", "Q3"):
        finish(store, key)
    assert store.delivery_ready(store.snapshot())
    assert len(store.registry_payload()['results'])==3
    assert store.publish_paper('# 已完成部分\n实际结果：42')[0]=='paper.md'
    workspace_path(tmp_path, ".system/graph_thread.json").write_text(json.dumps({"thread_id": "research", "engine_version": "research-v3", "status": "running"}))
    events = []
    class Stream:
        async def send_json_block(self, kind, content): events.append((kind, content))
    ctx = GraphContext.create(str(tmp_path), planner_llm=None, stream_manager=Stream())
    ctx.research = store
    asyncio.run(ctx.set_stage("write", "in_progress", "writing existing results"))
    assert any(kind == "research_state" and content["delivery_stages"]["write"]["status"] == "in_progress" for kind, content in events)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    snapshot = research_snapshot(tmp_path, "work", "paused")
    assert all(t["progress"]["state"] == "completed" for t in snapshot.experiments)
    assert snapshot.experiments[0]['acceptance']=='rejected' and snapshot.faults
    assert snapshot.delivery_stages["write"]["status"] == "interrupted"
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    store.recover()
    assert public_snapshot(store.snapshot())["delivery_stages"]["write"]["status"] == "pending"
