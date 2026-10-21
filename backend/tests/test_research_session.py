"""Research actions must not mutate evidence during ordinary follow-up questions."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth.auth import get_current_user
from database.database import get_db
from routers.chat_routes import chat
from services.chat_services.research_session import (
    research_snapshot, resolve_action, answer_research_question, scoped_snapshot, infer_question_scope,
)
from ai_system.judge.base import Answers, ChoiceAnswer


class ReadOnlyChoice:
    async def ask(self, state, questions):
        return Answers(judge='jev:offline', choices={
            'intent': ChoiceAnswer('question', {'question': 1}, None)})


def seed(root):
    (root / ".system").mkdir()
    run = root / "runs/run_one"
    run.mkdir(parents=True)
    (run / "input.py").write_text("print(0.82)")
    (run / "stdout.log").write_text("AUC=0.82")
    (root / "paper.md").write_text("# 研究\n已有报告，不得改写")
    (root / ".system/graph_thread.json").write_text(json.dumps({"thread_id": "research-1", "engine_version": "research-v3", "status": "done"}))
    (root / ".system/results.json").write_text(json.dumps({
        "results": [
            {"id": "R1", "subproblem": "Q1", "name": "AUC", "value": "0.82", "run_id": "run_one"},
            {"id": "R2", "subproblem": "Q2", "name": "Count", "value": "42", "run_id": "run_one"},
        ],
        "claims": [{"id": "C1", "subproblem": "Q1", "statement": "结果 {{R:R1}}", "strength": "倾向", "evidence": ["R1"]}],
    }))
    (root / "manifest.json").write_text(json.dumps({
        "api_key": "must-not-be-exposed", "runs": {
            "run_one": {"status": "success", "input": "runs/run_one/input.py", "stdout": "runs/run_one/stdout.log"},
        },
    }))
    from ai_system.research.store import ResearchStore
    from ai_system.research.models import WorkerResult
    from services.migrate_data import migrate_project
    store=ResearchStore(root,'research-1')
    question='检查研究结果与样本数量'
    store.initialize(question,{'subproblems':[{'id':key,'title':key,'question':question,'depends_on':[]} for key in ('Q1','Q2')],
        'requirements':[{'id':'RQ'+str(i),'statement':question,'source_quote':question,'kind':'goal','task_ids':[key],'checks':[]} for i,key in enumerate(('Q1','Q2'),1)],'assumptions':[]})
    projection=json.loads((root/'.system/results.json').read_text())
    for i,key in enumerate(('Q1','Q2')):
        row={'kind':'number','unit':'','description':'','path':None,**projection['results'][i]}
        order=store.claim(key)
        store.commit(WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',
            report={'status':'completed','requirement_evidence':[{'requirement_id':'RQ'+str(i+1),'evidence_refs':[row['id']]}]},
            results=[row],claims=projection['claims'] if key=='Q1' else []))
    from research_tools_helpers import answer
    current=store.snapshot()
    for i,key in enumerate(('Q1','Q2')):
        answer(store,coverage='answered',stop_reason='已回答本次给定样本范围，推广证据仍未知',question_id=current['experiments'][key]['question_id'],
            statement='本次记录显示 {{R:'+projection['results'][i]['id']+'}}，仅限所提供样本',
            artifact_refs=[projection['results'][i]['id']],limitations=['没有外推证据'])
    store.publish_paper('# 研究\n已有报告，不得改写')
    migrate_project(root)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("requested,message,status,graph_status,expected", [
    ("run", "重新计算", "completed", "done", "run"),
    ("revise", "换用另一种方法", "waiting", "interrupted", "revise"),
    ("ask", "继续", "failed", "done", "ask"),
])
def test_action_resolution_is_safe_and_explicit(tmp_path, requested, message, status, graph_status, expected):
    seed(tmp_path)
    snapshot = research_snapshot(tmp_path, "work", status)
    snapshot.graph_status = graph_status
    assert asyncio.run(resolve_action(requested, message, snapshot)) == expected


def test_invalid_controls_never_start_a_graph(tmp_path):
    snapshot = research_snapshot(tmp_path, "work", "created")
    assert asyncio.run(resolve_action("ask", "你能做什么", snapshot)) == "ask"
    for action in ("confirm", "resume", "revise", "unsupported", []):
        with pytest.raises(ValueError):
            asyncio.run(resolve_action(action, "继续", snapshot))


def test_snapshot_scopes_real_results_and_allowlists_manifest_paths(tmp_path):
    original = seed(tmp_path)
    snapshot = research_snapshot(tmp_path, "work", "completed")
    assert snapshot.research_id == "research-1"
    assert snapshot.runs[0].input_path == "runs/run_one/input.py"
    assert snapshot.results[0].subproblem_id == "Q1"
    assert "must-not-be-exposed" not in snapshot.model_dump_json()
    selected = scoped_snapshot(snapshot, "Q1")
    assert [r.id for r in selected.results] == ["R1"]
    with pytest.raises(ValueError):
        scoped_snapshot(snapshot, "Q99")
    assert original == {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    data = json.loads((tmp_path / "manifest.json").read_text())
    data["runs"]["run_one"]["input"] = ".system/graph_thread.json"
    data["runs"]["run_one"]["stdout"] = "../outside.log"
    (tmp_path / "manifest.json").write_text(json.dumps(data))
    assert research_snapshot(tmp_path, "work", "completed").runs[0].input_path is None
    assert research_snapshot(tmp_path, "work", "completed").runs[0].stdout_path is None


def test_corrupt_projection_cannot_override_authoritative_results(tmp_path):
    seed(tmp_path)
    (tmp_path / ".system/results.json").write_text("{")
    assert len(research_snapshot(tmp_path,"work","completed").results)==2


@pytest.mark.parametrize('destination', ['https://api.deepseek.com/v1', 'https://aigw.saurlax.com/v1'])
def test_read_only_question_streams_scoped_evidence_and_preserves_every_file(tmp_path, monkeypatch, destination):
    monkeypatch.setenv("AI_RESEARCH_CONTEXT_ENABLED", "1")
    original = seed(tmp_path)
    requests, blocks, output = [], [], []
    class Model:
        async def astream(self, messages):
            requests.extend(messages)
            yield SimpleNamespace(content="AUC 为 0.82 [R1]")
    class Stream:
        async def send_json_block(self, kind, content): blocks.append((kind, content))
        async def _output(self, content): output.append(content)
    asyncio.run(answer_research_question(
        Model(), Stream(), research_snapshot(tmp_path, "work", "completed"), tmp_path,
        "解释 Q1", [{"role": "user", "content": "这个模型怎么样"}], "Q1", destination_url=destination,
    ))
    assert output == ["AUC 为 0.82 [R1]"]
    assert blocks[0][0] == "research_context"
    assert blocks[0][1]["result_ids"] == ["R1"]
    assert "R2" not in requests[-1].content
    assert "只读" in requests[0].content
    assert original == {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.fixture
def web(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_RESEARCH_CONTEXT_ENABLED", "1")
    seed(tmp_path)
    work = SimpleNamespace(created_by=7, status="completed", template_id=None, output_mode="markdown")
    calls, saved, statuses = [], [], []
    class Service:
        def __init__(self, db): pass
        def create_or_get_work_session(self, *args): return SimpleNamespace(session_id="main")
        def get_work_chat_history(self, *args): return [{"role": "user", "content": "原问题"}]
        def add_message(self, *args, **kwargs): saved.append(args)
        def add_json_card_message(self, *args, **kwargs): saved.append(args)
    class Model:
        async def astream(self, messages):
            calls.append(("ask", messages))
            yield SimpleNamespace(content="已有数值是 0.82 [R1]")
    class Agent:
        def __init__(self, llm, stream, *args, **kwargs):
            calls.append(("graph", None))
            self.stream = stream
            self.last_result = {"solutions": {"Q1": {"status": "completed"}}}
            self.waiting_confirm = False
        async def run(self, text, **kwargs):
            calls.append(("run", (text, kwargs)))
            await self.stream.send_json_block("main_agent_complete", "已完成")
        async def resume_failed(self):
            calls.append(("resume", None))
            await self.stream.send_json_block("main_agent_complete", "已恢复")
    monkeypatch.setattr(chat, "verify_token", lambda token: 7)
    monkeypatch.setattr(chat, "get_db", lambda: iter([SimpleNamespace(close=lambda: None)]))
    monkeypatch.setattr("services.data_services.crud.get_work", lambda *args: work)
    def update(db, work_id, status):
        statuses.append(status)
        work.status = status
    monkeypatch.setattr("services.data_services.crud.update_work_status", update)
    monkeypatch.setattr(chat, "ChatService", Service)
    monkeypatch.setattr(chat, "PaperGraphAgent", Agent)
    monkeypatch.setattr(chat, "get_workspace_path", lambda work_id: tmp_path)
    monkeypatch.setattr(chat, "LLMHandler", lambda **kwargs: SimpleNamespace(get_llm_instance=lambda: Model()))
    monkeypatch.setattr(chat, "setup_environment_from_db", lambda *args: SimpleNamespace(
        get_workspace_dir=lambda: str(tmp_path), config_manager=SimpleNamespace(get_model_config=lambda *args: SimpleNamespace(base_url="https://api.deepseek.com/v1") if args[0] == "brain" else None),
    ))
    chat.task_manager._tasks.clear()
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: 7
    app.dependency_overrides[get_db] = lambda: None
    with TestClient(app) as client:
        yield client, work, calls, statuses, saved, tmp_path
    chat.task_manager._tasks.clear()


def exchange(client, payload):
    with client.websocket_connect("/api/chat/ws/work") as ws:
        ws.send_json({"token": "test"})
        assert ws.receive_json()["type"] == "auth_success"
        ws.send_json(payload)
        events = []
        for _ in range(20):
            event = ws.receive_json()
            events.append(event)
            if event["type"] in {"complete", "error"}:
                break
        return events


def test_websocket_followup_never_constructs_graph_or_changes_research_status(web, monkeypatch):
    monkeypatch.setattr('services.chat_services.research_session.jev_available', lambda: True)
    monkeypatch.setattr('services.chat_services.research_session.JevJudge', lambda **kwargs: ReadOnlyChoice())
    client, work, calls, statuses, saved, root = web
    original = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    events = exchange(client, {"problem": "解释 Q1 的结果", "subproblem_id": "Q1"})
    assert events[0]["action"] == "ask"
    assert events[-1]["work_status"] == "completed"
    assert [name for name, _ in calls] == ["ask"]
    assert statuses == [] and work.status == "completed"
    context = next(e["block"] for e in events if e["type"] == "json_block")
    assert context["work_id"] == "work" and context["task_id"] == events[0]["task_id"] and context["schema_version"] == 1
    assert any("research_context" in str(args) for args in saved)
    assert original == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("action,expected", [("run", "run"), ("resume", "resume"), ("confirm", "run"), ("revise", "run")])
def test_explicit_research_actions_reach_the_right_graph_entry(web, action, expected):
    client, work, calls, statuses, saved, root = web
    if action in {"confirm", "revise"}:
        work.status = "waiting"
        (root / ".system/graph_thread.json").write_text(json.dumps({"thread_id": "research-1", "engine_version": "research-v3", "status": "interrupted"}))
    elif action == "resume":
        work.status = "failed"
    events = exchange(client, {"problem": "换一种方法", "action": action})
    assert events[0]["action"] == action and events[-1]["type"] == "complete"
    assert [name for name, _ in calls] == ["graph", expected]
    if action == "run":
        assert calls[-1][1][1] == {"action": "run", "new_run": True}
    if action == "confirm":
        assert calls[-1][1][0] == "换一种方法"
    evidence = next(e["block"]["content"] for e in events if e["type"] == "json_block" and e["block"]["type"] == "research_snapshot")
    assert evidence["results"][0]["run_id"] == "run_one"
    assert statuses == ["in_progress", "completed"]


def test_snapshot_api_checks_ownership_before_reading_files(web):
    client, work, *_ = web
    assert client.get("/api/chat/work/work/research").json()["results"][0]["id"] == "R1"
    work.created_by = 9
    response = client.get("/api/chat/work/work/research")
    assert response.status_code == 403 and "R1" not in response.text


def test_outcomes_api_preserves_the_same_owner_boundary(web):
    client, work, *_ = web
    response = client.get('/api/chat/work/work/outcomes')
    assert response.status_code == 200
    assert response.json()['outcomes'][0]['checking'] == 'unreviewed'
    assert response.json()['outcomes'][0]['results'][0]['id'] == 'R1'
    work.created_by = 9
    denied = client.get('/api/chat/work/work/outcomes')
    assert denied.status_code == 403 and 'R1' not in denied.text


def test_snapshot_does_not_show_a_lost_coordinator_as_running(web):
    client, work, *_ = web
    work.status = "in_progress"
    assert client.get("/api/chat/work/work/research").json()["work_status"] == "paused"
    assert work.status == "in_progress", "progress projection must not mutate business records"


def test_corrupt_records_reject_message_without_running_or_resetting(web):
    client, work, calls, statuses, saved, root = web
    import sqlite3
    with sqlite3.connect(root/'.system/research.sqlite') as db:db.execute("UPDATE research SET payload='{' WHERE id='research-1'")
    events = exchange(client, {"problem": "解释结果"})
    assert events[-1]["type"] == "error"
    assert not calls and not statuses and not saved
    with sqlite3.connect(root/'.system/research.sqlite') as db:assert db.execute('SELECT payload FROM research').fetchone()[0]=='{'




def test_question_data_sending_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_RESEARCH_CONTEXT_ENABLED", raising=False)
    seed(tmp_path)
    class NeverCalled:
        def __getattr__(self, name):
            pytest.fail("No model or stream calls are permitted without opt-in")
    with pytest.raises(PermissionError, match="未启用"):
        asyncio.run(answer_research_question(NeverCalled(), NeverCalled(),
            research_snapshot(tmp_path, "work", "completed"), tmp_path, "解释结果", []))


@pytest.mark.parametrize("destination", ["https://example.test/v1", "https://api.deepseek.com.evil.test", "http://api.deepseek.com", "https://api.deepseek.com@evil.test", "", "http://aigw.saurlax.com/v1", "https://aigw.saurlax.com.evil.test/v1", "https://aigw.saurlax.com:444/v1", "https://aigw.saurlax.com@evil.test/v1"])
def test_approval_does_not_extend_to_other_model_destinations(tmp_path, monkeypatch, destination):
    monkeypatch.setenv("AI_RESEARCH_CONTEXT_ENABLED", "1")
    seed(tmp_path)
    with pytest.raises(PermissionError, match="仅授权"):
        asyncio.run(answer_research_question(None, None, research_snapshot(tmp_path, "work", "completed"),
            tmp_path, "解释结果", [], destination_url=destination))

def test_question_failure_preserves_research_status_and_hides_provider_error(web, monkeypatch):
    client, work, calls, statuses, saved, root = web
    async def fail(*args, **kwargs):
        raise RuntimeError("provider-secret-must-not-be-exposed")
    monkeypatch.setattr(chat, "answer_research_question", fail)
    events = exchange(client, {"problem": "解释结果", "action": "ask"})
    assert events[-1]["type"] == "error" and events[-1]["work_status"] == "completed"
    assert "provider-secret" not in json.dumps(events)
    assert not statuses and work.status == "completed"
    assert (root / "paper.md").read_text() == "# 研究\n已有报告，不得改写"


def test_asking_about_waiting_plan_does_not_confirm_or_redesign(web, monkeypatch):
    monkeypatch.setattr('services.chat_services.research_session.jev_available', lambda: True)
    monkeypatch.setattr('services.chat_services.research_session.JevJudge', lambda **kwargs: ReadOnlyChoice())
    client, work, calls, statuses, saved, root = web
    work.status = "waiting"
    (root / ".system/graph_thread.json").write_text(json.dumps({"thread_id": "research-1", "engine_version": "research-v3", "status": "interrupted"}))
    events = exchange(client, {"problem": "解释这个方案有什么风险"})
    assert [name for name, _ in calls] == ["ask"]
    assert events[-1]["work_status"] == "waiting" and statuses == []
    assert json.loads((root / ".system/graph_thread.json").read_text())["status"] == "interrupted"


def test_stopping_reply_saves_partial_answer_without_pausing_the_completed_research(web, monkeypatch):
    client, work, calls, statuses, saved, root = web
    class Model:
        async def astream(self, messages):
            yield SimpleNamespace(content="已有结果")
            await asyncio.Event().wait()
    monkeypatch.setattr(chat, "LLMHandler", lambda **kwargs: SimpleNamespace(get_llm_instance=lambda: Model()))
    with client.websocket_connect("/api/chat/ws/work") as ws:
        ws.send_json({"token": "test"})
        assert ws.receive_json()["type"] == "auth_success"
        ws.send_json({"problem": "解释结果", "action": "ask"})
        assert ws.receive_json()["type"] == "start"
        assert ws.receive_json()["block"]["type"] == "research_context"
        assert ws.receive_json()["content"] == "已有结果"
        response = client.post("/api/chat/work/work/pause")
        assert response.json() == {"work_status": "completed"}
        assert ws.receive_json()["work_status"] == "completed"
    assert not statuses and work.status == "completed"
    assert any("回复已停止" in str(args) for args in saved)



@pytest.mark.parametrize("question,expected", [("解释Q2的结果", "Q2"), ("q1 怎么得出来的", "Q1"), ("比较Q1与Q2", None), ("解释Q999", None)])
def test_single_mentioned_subproblem_selects_existing_evidence(tmp_path, question, expected):
    seed(tmp_path)
    assert infer_question_scope(question, research_snapshot(tmp_path, "work", "completed")) == expected

def test_unscoped_question_balances_subproblems_and_includes_actual_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_RESEARCH_CONTEXT_ENABLED", "1")
    seed(tmp_path)
    (tmp_path / ".system/validation.json").write_text(json.dumps({"issues": [{"section": "Q2", "message": "缺少基线说明"}]}))
    from ai_system.research.store import ResearchStore
    from ai_system.research.models import IssueReport
    ResearchStore(tmp_path,'research-1').report_issues([IssueReport(task_id='Q2',category='writing',section='Q2',message='缺少基线说明')],'sections')
    snapshot = research_snapshot(tmp_path, "work", "completed")
    q2 = snapshot.results[1]
    snapshot.results = [
        snapshot.results[0].model_copy(update={"id": f"R{i}", "name": f"metric{i}"})
        for i in range(10, 110)
    ] + [q2]
    captured, blocks = [], []
    class Model:
        async def astream(self, messages):
            captured.extend(messages)
            yield SimpleNamespace(content="需要复核基线")
    class Stream:
        async def send_json_block(self, kind, content): blocks.append(content)
        async def _output(self, content): pass
    asyncio.run(answer_research_question(Model(), Stream(), snapshot, tmp_path,
        "总结已有研究", [], destination_url="https://api.deepseek.com/v1"))
    assert "R2" in blocks[0]["result_ids"] and len(blocks[0]["result_ids"]) == 80
    assert blocks[0]["truncated"]
    assert "research:feedback" in blocks[0]["files"]
    assert "缺少基线说明" in captured[-1].content



@pytest.mark.parametrize("action", ["resume", "confirm", "revise"])
def test_unversioned_history_is_read_only_and_cannot_enter_graph(tmp_path, action):
    seed(tmp_path)
    thread = tmp_path / ".system/graph_thread.json"
    metadata = json.loads(thread.read_text())
    metadata.pop("engine_version")
    thread.write_text(json.dumps(metadata))
    snapshot = research_snapshot(tmp_path, "work", "waiting")
    assert not snapshot.results and snapshot.legacy_records and snapshot.engine_version is None
    assert asyncio.run(resolve_action("ask", "解释已有结果", snapshot)) == "ask"
    with pytest.raises(ValueError, match="不支持恢复"):
        asyncio.run(resolve_action(action, "继续", snapshot))
