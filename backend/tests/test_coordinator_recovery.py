"""Ownership survives model waits, excludes other workers, and dies with its process."""
import asyncio
import json
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.auth import get_current_user
from database.database import get_db
from models.models import Base, Project
from routers.chat_routes import chat
from services.chat_services.coordinator import claim, recover_orphaned_work
from services.chat_services.task_manager import task_manager


@pytest.fixture
def project_api(tmp_path, monkeypatch):
    from helpers.paper import accepted_store
    root = tmp_path / 'project'
    store=accepted_store(root)
    from services.file_services.workspace_layout import workspace_path
    workspace_path(root, ".system/graph_thread.json").write_text(json.dumps({
        "thread_id": store.research_id, "engine_version": "research-v3"}))
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(Project(work_id='project', title='study', created_by=7, status='in_progress'))
        db.commit()
    def database():
        with sessions() as db:
            yield db
    monkeypatch.setattr(task_manager, '_tasks', {})
    monkeypatch.setattr(chat, 'get_workspace_path', lambda work_id: root)
    monkeypatch.setattr(chat, 'get_db', database)
    monkeypatch.setattr(chat, 'verify_token', lambda token: 7)
    monkeypatch.setattr(chat, 'is_active_user', lambda db, user_id: True)
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: 7
    with TestClient(app) as client:
        yield client, sessions, root
    for task in task_manager._tasks.values():
        task_manager._release_ownership(task)
    engine.dispose()


def start_owner(root):
    # Child is a backend with no sandbox operation in progress (e.g. an LLM wait).
    stream = claim(root)
    path = stream.name
    stream.close()
    child = subprocess.Popen([sys.executable, '-c',
        'import fcntl,sys; f=open(sys.argv[1],"a+b"); fcntl.flock(f,fcntl.LOCK_EX); '
        'print("owned",flush=True); sys.stdin.read()', path], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert child.stdout.readline().strip() == b'owned'
    return child


def test_sigkill_owner_reenables_resume_through_websocket_and_snapshot(project_api, monkeypatch):
    client, sessions, root = project_api
    child = start_owner(root)
    try:
        assert client.get('/api/chat/work/project/research').json()['work_status'] == 'in_progress'
        assert client.post('/api/chat/work/project/pause').status_code == 409
        with pytest.raises(ValueError, match='其他后端'):
            task_manager.create_task('project', 7, 'duplicate', workspace=root)
        child.kill()  # No cooperative cleanup or graceful shutdown.
        child.wait(timeout=5)
        async def resumed(work_id, user_id, problem, action, snapshot, *args):
            assert action == 'resume' and snapshot.work_status == 'paused'
            task_manager.complete_task(work_id)
            await chat.manager.send(work_id, {'type': 'resumed', 'research_id': snapshot.research_id})
        monkeypatch.setattr(chat, '_execute_chat_task', resumed)
        with client.websocket_connect('/api/chat/ws/project') as ws:
            ws.send_json({'token':'test'})
            auth = ws.receive_json()
            assert auth['type'] == 'auth_success' and auth['work_status'] == 'paused'
            ws.send_json({'problem':'继续','action':'resume'})
            reply=ws.receive_json()
            assert reply['type'] == 'resumed', reply
        snapshot=client.get('/api/chat/work/project/research').json()
        assert snapshot['work_status']=='paused' and snapshot['research_id']
        with sessions() as db:
            assert db.query(Project).one().status == 'paused'
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


@pytest.mark.parametrize('entry', ['research', 'pause', 'task-status'])
def test_each_recovery_entry_repairs_unowned_state(project_api, entry):
    client, _, _ = project_api
    response = (client.post if entry=='pause' else client.get)('/api/chat/work/project/'+entry)
    assert response.status_code==200 and response.json()['work_status']=='paused'


def test_stale_reader_cannot_overwrite_owner_completion(project_api):
    _, sessions, root = project_api
    with sessions() as old:
        work=old.query(Project).one()
        with sessions() as writer:
            writer.query(Project).update({'status':'completed'})
            writer.commit()
        assert work.status=='in_progress'
        recover_orphaned_work(old,work,root)
        assert work.status=='completed'


def test_cancelled_task_keeps_ownership_until_cleanup_finishes(tmp_path, monkeypatch):
    async def check():
        monkeypatch.setattr(task_manager,'_tasks',{})
        entered=asyncio.Event()
        finish=asyncio.Event()
        async def runner():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                await finish.wait()
        task=task_manager.create_task('work',7,'run',workspace=tmp_path)
        running=asyncio.create_task(runner())
        task_manager.set_async_task('work',running)
        await entered.wait()
        task_manager.cancel_task('work')
        await asyncio.sleep(0)
        assert claim(tmp_path) is None
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await running
        await asyncio.sleep(0)
        lease=claim(tmp_path)
        assert lease is not None
        lease.close()
    asyncio.run(check())
