import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.chat_services.task_manager import TaskManager, TaskStatus


def _fresh_manager() -> TaskManager:
    manager = TaskManager()
    manager._tasks.clear()
    return manager


def test_outputs_get_increasing_seq_and_resume_after_seq():
    manager = _fresh_manager()
    manager.create_task("w1", 1, "题目")
    manager.start_task("w1")
    seqs = [manager.add_output("w1", "json_block", {"type": "paper_stage", "content": str(i)}) for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]
    assert [o.seq for o in manager.get_outputs_after("w1", 3)] == [4, 5]


def test_only_latest_plan_update_is_kept_for_replay():
    manager = _fresh_manager()
    manager.create_task("w2", 1, "题目")
    manager.add_output("w2", "json_block", {"type": "plan_updated", "content": {"v": 1}})
    manager.add_output("w2", "json_block", {"type": "paper_stage", "content": "分析"})
    manager.add_output("w2", "json_block", {"type": "plan_updated", "content": {"v": 2}})
    replay = manager.get_outputs_after("w2", 0)
    assert [o.data["type"] for o in replay] == ["paper_stage", "plan_updated"]
    assert replay[-1].data["content"] == {"v": 2} and replay[-1].seq == 3


def test_long_running_task_is_not_killed_by_age():
    """完整流水线常超过半小时：只要协程还在跑，就不能被判失败"""

    async def scenario():
        manager = _fresh_manager()
        manager.create_task("w3", 1, "题目")
        manager.start_task("w3")
        worker = asyncio.create_task(asyncio.sleep(10))
        manager.set_async_task("w3", worker)
        manager._tasks["w3"].started_at = time.time() - 3 * 3600
        assert manager.get_running_task("w3") is not None
        assert manager.get_task_status("w3")["status"] == "running"
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        # 协程已经结束但状态没更新（异常退出）时才判失败
        assert manager.get_running_task("w3") is None
        assert manager._tasks["w3"].status == TaskStatus.FAILED

    asyncio.run(scenario())


def test_websocket_reconnect_replays_only_after_last_seq(monkeypatch):
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import routers.chat_routes.chat as chat_module

    monkeypatch.setattr(chat_module, "verify_token", lambda token: 7)
    monkeypatch.setattr("services.data_services.crud.get_work", lambda db, work_id: SimpleNamespace(created_by=7))
    monkeypatch.setattr(chat_module, "get_db", lambda: iter([SimpleNamespace(close=lambda: None)]))

    manager = chat_module.task_manager
    manager._tasks.clear()
    task = manager.create_task("w9", 7, "题目")
    manager.start_task("w9")
    for i in range(4):
        manager.add_output("w9", "json_block", {"type": "paper_stage", "content": f"步骤{i + 1}"})

    app = FastAPI()
    app.include_router(chat_module.router)
    with TestClient(app).websocket_connect("/api/chat/ws/w9") as ws:
        ws.send_text(json.dumps({"token": "t", "task_id": task.task_id, "last_seq": 2}))
        assert json.loads(ws.receive_text())["type"] == "auth_success"
        assert json.loads(ws.receive_text())["type"] == "reconnect"
        replayed = [json.loads(ws.receive_text()) for _ in range(2)]
        assert [m["seq"] for m in replayed] == [3, 4]
        assert [m["block"]["content"] for m in replayed] == ["步骤3", "步骤4"]
        assert json.loads(ws.receive_text())["type"] == "reconnect_complete"
    manager._tasks.clear()


def test_set_work_status_updates_and_never_raises(monkeypatch):
    from routers.chat_routes import chat as chat_module
    calls, closed = [], []
    monkeypatch.setattr(chat_module, "get_db", lambda: iter([SimpleNamespace(close=lambda: closed.append(1))]))
    monkeypatch.setattr("services.data_services.crud.update_work_status",
                        lambda db, work_id, status: calls.append((work_id, status)))
    chat_module.set_work_status("w1", "waiting")
    assert calls == [("w1", "waiting")] and closed == [1]

    def boom(db, work_id, status):
        raise RuntimeError("db down")
    monkeypatch.setattr("services.data_services.crud.update_work_status", boom)
    chat_module.set_work_status("w1", "failed")  # 只记日志
    assert closed == [1, 1]
