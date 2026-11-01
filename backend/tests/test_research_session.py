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
        "解释 Q1", [{"role": "user", "content": "这个模型怎么样"}], "Q1",
    ))
    assert output == ["AUC 为 0.82 [R1]"]
    assert blocks[0][0] == "research_context"
    assert blocks[0][1]["result_ids"] == ["R1"]
    assert "R2" not in requests[-1].content
    assert "只读" in requests[0].content
    assert original == workspace_bytes(tmp_path)


@pytest.fixture
def web(monkeypatch, tmp_path):
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
    async def classify(*args, **kwargs):
        from ai_system.graph.message_graph import MessageIntent
        calls.append(('classify', None))
        return MessageIntent(intent='answer')
    monkeypatch.setattr('ai_system.graph.message_graph.classify_message', classify)
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


def test_websocket_auto_classifies_before_readonly_answer(web,monkeypatch):
    client,work,calls,statuses,saved,root=web
    qid=research_snapshot(root,'work','completed').questions[0]['id']
    events=exchange(client,{'problem':'解释这个结果，并指出局限','subproblem_id':qid})
    assert events[0]['action']=='auto' and events[-1]['type']=='complete'
    assert [name for name,_ in calls]==['classify','ask']
    assert next(e for e in events if e['type']=='message_routed')['action']=='ask'
    assert statuses==[]


def test_question_while_waiting_preserves_pending_clarification(web):
    from ai_system.research.store import ResearchStore
    client,work,calls,statuses,saved,root=web
    store=ResearchStore(root,'research-1')
    with store._transaction() as db:
        state=store._read(db)
        state['action']={'id':'waiting-year','capability':'ask','status':'waiting','inputs':{'message':'使用哪一年？'}}
        store._save(db,state,'test_waiting',{})
    work.status='waiting'
    original=store.snapshot()
    events=exchange(client,{'problem':'为什么需要年份？','request_id':'explain-year'})
    assert events[-1]['type']=='complete' and events[-1]['work_status']=='waiting'
    assert [name for name,_ in calls]==['classify','ask'] and statuses==[]
    assert store.snapshot()==original


def test_paper_reference_uses_same_socket_and_readonly_route(web):
    from services.chat_services import paper_project as project
    client,work,calls,statuses,saved,root=web
    doc=project.snapshot(root)
    text=next(f['text'] for f in doc['files'] if f['path']=='paper.md')
    context={'research_id':'research-1','references':[{'quote':text,'selection':{
        'file':'paper.md','version':doc['version'],'start':0,'end':len(text),'quote':text}}]}
    events=exchange(client,{'problem':'解释所引用的原文','request_id':'quoted-question','paper_context':context})
    assert events[-1]['type']=='complete' and statuses==[]
    assert [name for name,_ in calls]==['classify','ask']
    payload=json.loads(calls[-1][1][-1].content.split('当前工作材料：\n',1)[1].split('\n\n用户问题：',1)[0])
    assert payload['research']['paper_references'][0]['quote']==text
    from services.chat_services.chat_history_manager import ChatHistoryManager
    turn=next(m for m in ChatHistoryManager(root.parent).get_messages('work') if m['id']=='user:quoted-question')
    assert turn['metadata']['paper_context']['references'][0]['selection']['version']==doc['version']


def test_stale_reference_rejects_before_classifying_or_changing_status(web):
    from services.chat_services import paper_project as project
    client,work,calls,statuses,saved,root=web
    doc=project.snapshot(root);text=(root/'paper.md').read_text()
    context={'research_id':'research-1','references':[{'quote':text,'selection':{
        'file':'paper.md','version':doc['version'],'start':0,'end':len(text),'quote':text}}]}
    (root/'paper.md').write_text('人工修改的新正文')
    events=exchange(client,{'problem':'修改这里','paper_context':context})
    assert events[-1]['type']=='error' and events[-1]['scope']=='command'
    assert calls==[] and statuses==[]


@pytest.mark.parametrize('intent,expected', [('work','message'),('amend','revise_research'),('resume','resume'),('respond','run')])
def test_auto_dispatches_execution_after_classification(web,monkeypatch,intent,expected):
    from ai_system.graph.message_graph import MessageIntent
    from routers.chat_routes import chat
    client,work,calls,statuses,saved,root=web
    async def classify(*args,**kwargs):
        calls.append(('classify',None))
        assert statuses==[], 'classification must precede work status changes'
        return MessageIntent(intent=intent)
    monkeypatch.setattr('ai_system.graph.message_graph.classify_message',classify)
    async def revise(self,**kwargs):
        self.commit_completion()
        calls.append(('revise_research',kwargs))
        await self.stream.send_json_block('main_agent_complete','需求已更新')
    monkeypatch.setattr(chat.PaperGraphAgent,'revise_research',revise,raising=False)
    if intent=='resume':work.status='failed'
    if intent=='respond':work.status='waiting'
    events=exchange(client,{'problem':'使用2025年并继续','request_id':'execute-'+intent})
    assert events[0]['action']=='auto' and events[-1]['type']=='complete'
    assert [name for name,_ in calls]==['classify','graph',expected]
    assert statuses==['in_progress','completed']
    if intent=='amend':
        options=calls[-1][1]
        assert options['base_revision']>0 and options['command_id']=='user:execute-amend'
        assert options['replace_pending'] is True


def test_classification_failure_and_stop_preserve_research_state(web,monkeypatch):
    from routers.chat_routes import chat
    client,work,calls,statuses,saved,root=web
    entered=asyncio.Event()
    async def classify(*args,**kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr('ai_system.graph.message_graph.classify_message',classify)
    with client.websocket_connect('/api/chat/ws/work') as ws:
        ws.send_json({'token':'test'});ws.receive_json()
        ws.send_json({'problem':'先解释结果','request_id':'stop-routing'})
        start=ws.receive_json()
        assert start['type']=='start' and start['action']=='auto' and start['work_status']=='completed'
        client.portal.call(entered.wait)
        response=client.post('/api/chat/work/work/pause')
        assert response.json()['work_status']=='completed'
    assert calls==[] and statuses==[] and work.status=='completed'
    assert chat.task_manager.get_task('work').status.value=='cancelled'


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


def test_readonly_answer_uses_configured_model_without_legacy_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv('AI_RESEARCH_CONTEXT_ENABLED', raising=False)
    seed(tmp_path)
    seen=[]
    class Model:
        async def astream(self, messages):
            seen.extend(messages)
            yield SimpleNamespace(content='已有结果')
    class Stream:
        async def send_json_block(self, *args): pass
        async def _output(self, *args): pass
    asyncio.run(answer_research_question(Model(), Stream(), research_snapshot(tmp_path, 'work', 'completed'),
                                         tmp_path, '解释结果', []))
    assert seen


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
