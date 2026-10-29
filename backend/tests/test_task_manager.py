import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.chat_services.task_manager import TaskManager


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


def test_websocket_reconnect_replays_only_after_last_seq(monkeypatch):
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import routers.chat_routes.chat as chat_module

    monkeypatch.setattr(chat_module, "verify_token", lambda token: 7)
    monkeypatch.setattr("services.data_services.crud.get_work", lambda db, work_id: SimpleNamespace(created_by=7, architecture_version=2))
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
