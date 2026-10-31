"""Research actions must not mutate evidence during ordinary follow-up questions."""
from services.file_services.workspace_layout import workspace_path
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
from services.chat_services.research_session import research_snapshot, answer_research_question, scoped_snapshot
from services.chat_services.chat_history_manager import ChatHistoryManager
from services.chat_services.chat_service import ChatService as DurableChatService



def workspace_bytes(root):
    """Compare public files plus the state/evidence moved outside that tree."""
    from services.file_services.workspace_layout import private_root, evidence_root
    return {f'{kind}/{path.relative_to(folder).as_posix()}': path.read_bytes()
            for kind, folder in [('public', root), ('state', private_root(root)), ('evidence', evidence_root(root))]
            for path in folder.rglob('*') if path.is_file()}


def seed(root):
    workspace_path(root, '.system').mkdir(parents=True)
    run = workspace_path(root, 'runs/run_one')
    run.mkdir(parents=True)
    (run / "input.py").write_text("print(0.82)")
    (run / "stdout.log").write_text("AUC=0.82")
    (root / "paper.md").write_text("# 研究\n已有报告，不得改写")
    (workspace_path(root, '.system/graph_thread.json')).write_text(json.dumps({"thread_id": "research-1", "engine_version": "research-v3", "status": "done"}))
    (workspace_path(root, '.system/results.json')).write_text(json.dumps({
        "results": [
            {"id": "R1", "subproblem": "Q1", "name": "AUC", "value": "0.82", "run_id": "run_one"},
            {"id": "R2", "subproblem": "Q2", "name": "Count", "value": "42", "run_id": "run_one"},
        ],
        "claims": [{"id": "C1", "subproblem": "Q1", "statement": "结果 {{R:R1}}", "strength": "倾向", "evidence": ["R1"]}],
    }))
    (workspace_path(root, 'manifest.json')).write_text(json.dumps({
        "api_key": "must-not-be-exposed", "files": {}, "runs": {
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
    projection=json.loads((workspace_path(root, '.system/results.json')).read_text())
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
    return workspace_bytes(root)


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
    assert original == workspace_bytes(tmp_path)
    data = json.loads((workspace_path(tmp_path, 'manifest.json')).read_text())
    data["runs"]["run_one"]["input"] = ".system/graph_thread.json"
    data["runs"]["run_one"]["stdout"] = "../outside.log"
    (workspace_path(tmp_path, 'manifest.json')).write_text(json.dumps(data))
    assert research_snapshot(tmp_path, "work", "completed").runs[0].input_path is None
    assert research_snapshot(tmp_path, "work", "completed").runs[0].stdout_path is None


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
    assert original == workspace_bytes(tmp_path)


@pytest.fixture
def web(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_RESEARCH_CONTEXT_ENABLED", "1")
    root = tmp_path / 'work'
    root.mkdir()
    seed(root)
    work = SimpleNamespace(architecture_version=2, created_by=7, status="completed", template_id=None, output_mode="markdown")
    calls, saved, statuses = [], [], []
    class ObservedHistory(ChatHistoryManager):
        def upsert_reply(self, work_id, reply):
            super().upsert_reply(work_id, reply)
            # Capture actual committed checkpoints, not obsolete terminal save calls.
            saved.append(json.loads(json.dumps(reply)))
    history = ObservedHistory(str(tmp_path))
    history.save_message('work', 'user', '原问题')
    class Service(DurableChatService):
        def __init__(self, db):
            self.db_session = db
            self.history_manager = history
        def create_or_get_work_session(self, work_id, *args):
            return SimpleNamespace(session_id=f'{work_id}_main_session')
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
        def commit_completion(self):
            from ai_system.research.store import ResearchStore
            ResearchStore(root, 'research-1').set_outcome('completed')
        async def run(self, text, **kwargs):
            self.commit_completion()
            calls.append(("run", (text, kwargs)))
            await self.stream.send_json_block("main_agent_complete", "已完成")
        async def send_message(self,text,**kwargs):
            self.commit_completion()
            calls.append(('message',(text,kwargs)))
            await self.stream.send_json_block('main_agent_complete','已回复')
        async def resume_failed(self):
            self.commit_completion()
            calls.append(("resume", None))
            await self.stream.send_json_block("main_agent_complete", "已恢复")
    monkeypatch.setattr('services.chat_services.chat_history_manager.get_workspaces_path', lambda: tmp_path)
    monkeypatch.setattr('config.paths.get_workspace_path', lambda _: root)
    monkeypatch.setattr(chat, "verify_token", lambda token: 7)
    monkeypatch.setattr(chat, "get_db", lambda: iter([SimpleNamespace(close=lambda: None)]))
    monkeypatch.setattr("services.data_services.crud.get_work", lambda *args: work)
    def update(db, work_id, status):
        statuses.append(status)
        work.status = status
    monkeypatch.setattr("services.data_services.crud.update_work_status", update)
    monkeypatch.setattr(chat, "ChatService", Service)
    monkeypatch.setattr(chat, "PaperGraphAgent", Agent)
    monkeypatch.setattr(chat, "get_workspace_path", lambda work_id: root)
    monkeypatch.setattr(chat, "LLMHandler", lambda **kwargs: SimpleNamespace(get_llm_instance=lambda: Model()))
    monkeypatch.setattr(chat, "setup_environment_from_db", lambda *args: SimpleNamespace(
        get_workspace_dir=lambda: str(root), config_manager=SimpleNamespace(get_model_config=lambda *args: SimpleNamespace(base_url="https://api.deepseek.com/v1") if args[0] == "brain" else None),
    ))
    chat.task_manager._tasks.clear()
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: 7
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(close=lambda: None)
    with TestClient(app) as client:
        yield client, work, calls, statuses, saved, root
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


def research_files(root):
    import sqlite3
    from contextlib import closing
    files = {name: content for name, content in workspace_bytes(root).items()
             if name not in {'state/chat_history.json', 'state/chat_history.json.lock',
                             'state/.system/research.sqlite'}}
    with closing(sqlite3.connect(workspace_path(root, '.system/research.sqlite'))) as db:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if name.startswith('chat_') or name == 'sqlite_sequence': continue
            files['db:' + name] = db.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall()
    return files


def durable_reply(root, reply_id, status):
    messages = ChatHistoryManager(str(root.parent)).get_messages(root.name)
    replies = [m for m in messages if m['role'] == 'assistant']
    assert len(replies) == 1, 'checkpoints and terminal saves must upsert the same reply'
    reply = replies[0]
    assert reply['id'] == reply_id == reply['metadata']['reply_id']
    assert reply['metadata']['status'] == status
    assert reply['message_type'] == 'timeline' and reply['metadata']['chat_task_id']
    assert messages[0]['content'] == '原问题', 'incremental updates preserve previous history'
    return reply


def test_websocket_followup_never_constructs_graph_or_changes_research_status(web, monkeypatch):
    client, work, calls, statuses, saved, root = web
    original = research_files(root)
    events = exchange(client, {"problem": "解释 Q1 的结果", "action":"ask", "subproblem_id": "Q1"})
    assert events[0]["action"] == "ask"
    assert events[-1]["work_status"] == "completed"
    assert [name for name, _ in calls] == ["ask"]
    assert statuses == [] and work.status == "completed"
    context_update = next(e['update'] for e in events if e['type'] == 'reply_update' and
                          any(segment.get('block', {}).get('type') == 'research_context' for segment in e['update']['segments']))
    context = next(segment['block']['content'] for segment in context_update['segments']
                   if segment.get('block', {}).get('type') == 'research_context')
    assert context['work_id'] == 'work' and context['schema_version'] == 1
    assert context_update['chat_task_id'] == events[0]['task_id']
    reply = durable_reply(root, events[0]['reply_id'], 'completed')
    assert context_update['reply_id'] == reply['id']
    assert reply['content'] == '已有数值是 0.82 [R1]'
    assert 'research_context' in str(saved[-1]['segments'])
    assert original == research_files(root), 'conversation checkpoints must not mutate research evidence'


def test_websocket_auto_reaches_existing_agent_without_loading_jev(web,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('automatic chat must not load a classifier')
    monkeypatch.setattr('ai_system.judge.JevJudge',forbidden)
    client,work,calls,statuses,saved,root=web
    qid=research_snapshot(root,'work','completed').questions[0]['id']
    events=exchange(client,{'problem':'解释这个结果，并指出局限','subproblem_id':qid})
    assert events[0]['action']=='message' and events[-1]['type']=='complete'
    assert [name for name,_ in calls]==['graph','message']
    assert calls[-1][1]==('解释这个结果，并指出局限',{'question_id':qid})
    assert statuses==['in_progress','completed']


@pytest.mark.parametrize("action,expected", [("run", "run"), ("resume", "resume"), ("confirm", "run"), ("revise", "run")])
def test_explicit_research_actions_reach_the_right_graph_entry(web, action, expected):
    client, work, calls, statuses, saved, root = web
    if action in {"confirm", "revise"}:
        work.status = "waiting"
        (workspace_path(root, '.system/graph_thread.json')).write_text(json.dumps({"thread_id": "research-1", "engine_version": "research-v3", "status": "interrupted"}))
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
    reply = durable_reply(root, events[0]['reply_id'], 'completed')
    assert reply['metadata']['chat_task_id'] == events[0]['task_id']
    assert any(segment.get('block', {}).get('type') == 'main_agent_complete' for segment in reply['segments'])
    assert saved[0]['id'] == saved[-1]['id'] == reply['id']


def test_corrupt_records_reject_message_without_running_or_resetting(web):
    client, work, calls, statuses, saved, root = web
    import sqlite3
    with sqlite3.connect(workspace_path(root, '.system/research.sqlite')) as db:db.execute("UPDATE research SET payload='{' WHERE id='research-1'")
    events = exchange(client, {"problem": "解释结果"})
    assert events[-1]["type"] == "error"
    assert not calls and not statuses and not saved
    with sqlite3.connect(workspace_path(root, '.system/research.sqlite')) as db:assert db.execute('SELECT payload FROM research').fetchone()[0]=='{'


@pytest.mark.parametrize("destination", ["https://example.test/v1", "https://api.deepseek.com.evil.test", "http://api.deepseek.com", "https://api.deepseek.com@evil.test", "", "http://aigw.saurlax.com/v1", "https://aigw.saurlax.com.evil.test/v1", "https://aigw.saurlax.com:444/v1", "https://aigw.saurlax.com@evil.test/v1"])
def test_approval_does_not_extend_to_other_model_destinations(tmp_path, monkeypatch, destination):
    monkeypatch.setenv("AI_RESEARCH_CONTEXT_ENABLED", "1")
    seed(tmp_path)
    with pytest.raises(PermissionError, match="仅授权"):
        asyncio.run(answer_research_question(None, None, research_snapshot(tmp_path, "work", "completed"),
            tmp_path, "解释结果", [], destination_url=destination))


def test_multiple_browsers_observe_turns_reconnect_and_continue_after_submitter_leaves(web, monkeypatch):
    from routers.chat_routes import chat
    client, work, calls, statuses, saved, root = web
    gates = []

    async def answer(model, stream, *args, **kwargs):
        gate = asyncio.Event()
        gates.append(gate)
        await stream.send_json_block('paper_stage', 'live step')
        await gate.wait()
        await stream._output('shared answer')

    monkeypatch.setattr(chat, 'answer_research_question', answer)

    def receive_until(socket, kind):
        events = []
        for _ in range(30):
            event = socket.receive_json()
            events.append(event)
            if event['type'] == kind:
                return events
        pytest.fail(f'missing {kind}: {events}')

    with client.websocket_connect('/api/chat/ws/work') as observer:
        observer.send_json({'token': 'test'})
        assert observer.receive_json()['type'] == 'auth_success'
        with client.websocket_connect('/api/chat/ws/work') as author:
            author.send_json({'token': 'test'})
            assert author.receive_json()['type'] == 'auth_success'
            author.send_json({'problem': '解释结果', 'action': 'ask', 'request_id': 'one'})
            a = receive_until(author, 'start')[-1]
            b = receive_until(observer, 'start')[-1]
            assert a == b and a['request_id'] == 'one'
            for _ in range(10):
                event = observer.receive_json()
                if any(s.get('block', {}).get('type') == 'paper_stage' for s in event.get('update', {}).get('segments', [])):
                    break
            else: pytest.fail('live step missing')
            observer.send_json({'problem': 'competing ask', 'action': 'ask', 'request_id': 'two'})
            rejected = receive_until(observer, 'error')[-1]
            assert rejected['scope'] == 'command' and rejected['request_id'] == 'two'
            assert chat.task_manager.get_task('work').task_id == a['task_id']
            with client.websocket_connect('/api/chat/ws/work') as joining:
                joining.send_json({'token': 'test', 'task_id': a['task_id'], 'last_seq': event['seq'] - 1})
                replay = receive_until(joining, 'reconnect_complete')
                assert replay[0]['type'] == 'auth_success'
                assert [e['seq'] for e in replay if 'seq' in e] == [event['seq']]
                assert replay[-1]['reply_id'] == a['reply_id']
        assert chat.task_manager.get_task('work').status.value == 'running'
        client.portal.call(gates[0].set)
        terminal = receive_until(observer, 'complete')
        assert not any(e['type'] == 'error' for e in terminal)
        assert terminal[-1]['task_id'] == a['task_id']
        with client.websocket_connect('/api/chat/ws/work') as later:
            later.send_json({'token': 'test'})
            auth = later.receive_json()
            assert auth['task']['status'] == 'completed' and auth['reload_history']
            later.send_json({'problem': 'another question', 'action': 'ask', 'request_id': 'three'})
            start = receive_until(later, 'start')[-1]
            assert receive_until(observer, 'start')[-1] == start
            assert start['task_id'] != a['task_id'] and start['reply_id'] != a['reply_id']
            for _ in range(10):
                update = later.receive_json()
                if any(s.get('block', {}).get('type') == 'paper_stage' for s in update.get('update', {}).get('segments', [])):
                    break
            client.portal.call(gates[1].set)
            assert receive_until(later, 'complete')[-1] == receive_until(observer, 'complete')[-1]
    messages = ChatHistoryManager(str(root.parent)).get_messages(root.name)
    assert len([m for m in messages if m['role'] == 'assistant']) == 2
    assert not any(m['content'] == 'competing ask' for m in messages)
    assert chat.manager.get_connection_count() == 0
