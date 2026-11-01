"""The web controls must preserve credentials, ownership, and resumable work."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth.auth import get_current_user
from database.database import get_db
from models.models import Base, ModelConfig
from routers.config_routes import model_config as configs
from routers.chat_routes import chat
from services.chat_services.chat_service import ChatService as DurableChatService
from services.chat_services.chat_history_manager import ChatHistoryManager


@pytest.fixture
def config_client():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add_all([
            ModelConfig(type='brain',model_id='default',base_url='https://example.test/v1',api_key='owner-secret',created_by=7),
            ModelConfig(type='code',model_id='other',base_url='',api_key='other-secret',created_by=9),
        ])
        db.commit()
    app = FastAPI()
    app.include_router(configs.router)
    app.dependency_overrides[get_current_user] = lambda: 7
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield client, sessions
    engine.dispose()


def test_update_without_new_key_preserves_saved_key_and_checks_owner(config_client):
    client, sessions = config_client
    assert client.put('/model-configs/1',json={'model_id':'changed'}).status_code == 200
    assert client.put('/model-configs/2',json={'model_id':'forbidden'}).status_code == 403
    with sessions() as db:
        assert db.query(ModelConfig).filter_by(id=1).one().api_key == 'owner-secret'
        assert db.query(ModelConfig).filter_by(id=2).one().model_id == 'other'


@pytest.mark.parametrize('timeout',[False,True])
def test_connection_errors_never_return_provider_secrets(config_client,monkeypatch,timeout):
    client, _ = config_client
    error = asyncio.TimeoutError() if timeout else RuntimeError('request contained owner-secret')
    monkeypatch.setattr('ai_system.core_handlers.llm_providers.create_llm_from_model_config',lambda *a,**kw:SimpleNamespace(ainvoke=AsyncMock(side_effect=error)))
    response = client.post('/model-configs/test',json={'model_id':'default'})
    assert response.status_code == (504 if timeout else 400)
    assert 'owner-secret' not in response.text and 'request contained' not in response.text


def test_pause_waits_for_saved_partial_output_and_notifies_the_websocket(monkeypatch,tmp_path):
    work = SimpleNamespace(architecture_version=2, created_by=7,status='created',template_id=None,output_mode='markdown')
    root = tmp_path / 'pause-test'
    root.mkdir()
    history = ChatHistoryManager(str(tmp_path))
    class ChatService(DurableChatService):
        def __init__(self,db):
            self.db_session = db
            self.history_manager = history
        def create_or_get_work_session(self,work_id,*args):
            return SimpleNamespace(session_id=f'{work_id}_main_session')
    class Agent:
        def __init__(self,llm,stream,*args,**kwargs):self.stream=stream
        async def run(self,problem,**kwargs):
            await self.stream.send_json_block('paper_stage','Q4：求解中')
            await asyncio.Event().wait()
    monkeypatch.setattr(chat,'verify_token',lambda token:7)
    monkeypatch.setattr(chat,'get_db',lambda:iter([SimpleNamespace(close=lambda:None)]))
    monkeypatch.setattr('services.data_services.crud.get_work',lambda *args:work)
    monkeypatch.setattr('services.data_services.crud.update_work_status',lambda db,w,status:setattr(work,'status',status))
    monkeypatch.setattr(chat,'ChatService',ChatService)
    monkeypatch.setattr(chat,'PaperGraphAgent',Agent)
    monkeypatch.setattr(chat,'get_workspace_path',lambda work_id:root)
    monkeypatch.setattr(chat,'LLMHandler',lambda **kw:SimpleNamespace(get_llm_instance=lambda:None))
    monkeypatch.setattr(chat,'setup_environment_from_db',lambda *a:SimpleNamespace(get_workspace_dir=lambda:str(root),config_manager=SimpleNamespace(get_model_config=lambda *a:None)))
    manager = chat.task_manager
    manager._tasks.clear()
    app=FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user]=lambda:7
    app.dependency_overrides[get_db]=lambda:SimpleNamespace(close=lambda:None)
    with TestClient(app) as client:
        with client.websocket_connect('/api/chat/ws/pause-test') as ws:
            ws.send_json({'token':'test'})
            assert ws.receive_json()['type']=='auth_success'
            ws.send_json({'problem':'test','action':'run'})
            start = ws.receive_json()
            assert start['type'] == 'start'
            for _ in range(10):
                event = ws.receive_json()
                if event['type'] == 'message_routed':
                    assert event['action']=='run'
                    continue
                assert event['type'] == 'reply_update'
                if any(s.get('block', {}).get('type') == 'paper_stage' for s in event['update']['segments']): break
            else: pytest.fail('paper_stage checkpoint missing')
            running = ChatHistoryManager(str(root.parent)).get_messages(root.name)[-1]
            assert running['id'] == start['reply_id'] and running['metadata']['status'] == 'running'
            response=client.post('/api/chat/work/pause-test/pause')
            assert response.json()=={'work_status':'paused'}
            for _ in range(10):
                event = ws.receive_json()
                if event['type'] == 'complete': break
                assert event['type'] == 'reply_update'
            else: pytest.fail('terminal pause receipt missing')
            assert event['work_status']=='paused' and event['reload_history']
            assert work.status=='paused'
            messages = ChatHistoryManager(str(root.parent)).get_messages(root.name)
            replies = [message for message in messages if message['role'] == 'assistant']
            assert len(replies) == 1 and replies[0]['id'] == start['reply_id']
            assert replies[0]['metadata']['status'] == 'paused'
            assert '任务已暂停' in replies[0]['content'] and 'paper_stage' in str(replies[0]['segments'])
            assert any(message['role'] == 'user' and message['content'] == 'test' for message in messages)
        work.created_by=9
        assert client.post('/api/chat/work/pause-test/pause').status_code==403
    manager._tasks.clear()
