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


def test_apply_default_copies_saved_key_only_to_owned_roles(config_client):
    client, sessions = config_client
    response = client.post('/model-configs/apply-default')
    assert response.status_code == 200
    assert {c['type'] for c in response.json()} == {'brain','code','writing'}
    assert 'api_key' not in response.text and 'owner-secret' not in response.text
    with sessions() as db:
        assert all(c.api_key == 'owner-secret' and c.model_id == 'default' for c in db.query(ModelConfig).filter_by(created_by=7))
        assert db.query(ModelConfig).filter_by(created_by=9).one().api_key == 'other-secret'


def test_update_without_new_key_preserves_saved_key_and_checks_owner(config_client):
    client, sessions = config_client
    assert client.put('/model-configs/1',json={'model_id':'changed'}).status_code == 200
    assert client.put('/model-configs/2',json={'model_id':'forbidden'}).status_code == 403
    with sessions() as db:
        assert db.query(ModelConfig).filter_by(id=1).one().api_key == 'owner-secret'
        assert db.query(ModelConfig).filter_by(id=2).one().model_id == 'other'


def test_browser_saved_step_connection_reaches_all_roles_with_live_defaults(config_client):
    from ai_system.config.environment import DatabaseConfigManager
    from ai_system.core_handlers.llm_handler import LLMHandler
    from ai_system.core_handlers.llm_providers import create_llm_from_model_config
    client, sessions = config_client
    assert client.put('/model-configs/1', json={'model_id':'step-5-preview',
        'base_url':'https://aigw.saurlax.com/v1'}).status_code == 200
    assert client.post('/model-configs/apply-default').status_code == 200
    with sessions() as db:
        for role in ('brain', 'code', 'writing'):
            config = DatabaseConfigManager(db).get_model_config(role, 7)
            llm = (LLMHandler(model_config=config).get_llm_instance() if role == 'brain'
                   else create_llm_from_model_config(config))
            assert llm.extra_body == {'reasoning_effort':'low'}
            assert llm.request_timeout == 600 and llm.max_retries == 0


def test_step_connection_route_preserves_its_explicit_probe_budget(config_client, monkeypatch):
    from langchain_core.messages import AIMessage
    from ai_system.core_handlers import llm_providers
    client, _ = config_client
    captured = []
    original = llm_providers.create_llm_from_model_config
    def factory(config, **kwargs):
        llm = original(config, **kwargs)
        captured.append(llm)
        return llm
    monkeypatch.setattr(llm_providers, 'create_llm_from_model_config', factory)
    invoke = AsyncMock(return_value=AIMessage(content='OK'))
    monkeypatch.setattr(llm_providers.AIGWChatOpenAI, 'ainvoke', invoke)
    response = client.post('/model-configs/test', json={'model_id':'step-5-preview',
        'base_url':'https://aigw.saurlax.com/v1'})
    assert response.status_code == 200 and response.json() == {'ok':True}
    llm = captured[0]
    assert llm.extra_body == {'reasoning_effort':'low'}
    assert llm.request_timeout == 20 and llm.max_retries == 0 and llm.max_tokens == 32
    assert llm.streaming is False
    invoke.assert_awaited_once()


def test_connection_test_uses_owned_saved_key_and_makes_a_real_adapter_call(config_client,monkeypatch):
    client, _ = config_client
    captured = []
    invoke = AsyncMock(return_value=SimpleNamespace(content='OK'))
    def factory(config,**kwargs):
        captured.append((config,kwargs))
        return SimpleNamespace(ainvoke=invoke)
    monkeypatch.setattr('ai_system.core_handlers.llm_providers.create_llm_from_model_config',factory)
    response = client.post('/model-configs/test',json={'type':'brain','model_id':'default','base_url':'https://example.test/v1'})
    assert response.json() == {'ok':True}
    assert captured[0][0].api_key == 'owner-secret'
    assert captured[0][1]['max_retries'] == 0
    invoke.assert_awaited_once()
    # Another user's code key is not a fallback for the current user's unconfigured role.
    assert client.post('/model-configs/test',json={'type':'code','model_id':'default'}).status_code == 400
    assert len(captured) == 1


@pytest.mark.parametrize('timeout',[False,True])
def test_connection_errors_never_return_provider_secrets(config_client,monkeypatch,timeout):
    client, _ = config_client
    error = asyncio.TimeoutError() if timeout else RuntimeError('request contained owner-secret')
    monkeypatch.setattr('ai_system.core_handlers.llm_providers.create_llm_from_model_config',lambda *a,**kw:SimpleNamespace(ainvoke=AsyncMock(side_effect=error)))
    response = client.post('/model-configs/test',json={'model_id':'default'})
    assert response.status_code == (504 if timeout else 400)
    assert 'owner-secret' not in response.text and 'request contained' not in response.text


def test_pause_waits_for_saved_partial_output_and_notifies_the_websocket(monkeypatch,tmp_path):
    work = SimpleNamespace(created_by=7,status='created',template_id=None,output_mode='markdown')
    saved = []
    class ChatService:
        def __init__(self,db): pass
        def create_or_get_work_session(self,*args):return SimpleNamespace(session_id='main')
        def add_message(self,*args,**kwargs):saved.append(args)
        def add_json_card_message(self,*args,**kwargs):saved.append(args)
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
    monkeypatch.setattr(chat,'get_workspace_path',lambda work_id:tmp_path)
    monkeypatch.setattr(chat,'LLMHandler',lambda **kw:SimpleNamespace(get_llm_instance=lambda:None))
    monkeypatch.setattr(chat,'setup_environment_from_db',lambda *a:SimpleNamespace(get_workspace_dir=lambda:str(tmp_path),config_manager=SimpleNamespace(get_model_config=lambda *a:None)))
    manager = chat.task_manager
    manager._tasks.clear()
    app=FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user]=lambda:7
    app.dependency_overrides[get_db]=lambda:None
    with TestClient(app) as client:
        with client.websocket_connect('/api/chat/ws/pause-test') as ws:
            ws.send_json({'token':'test'})
            assert ws.receive_json()['type']=='auth_success'
            ws.send_json({'problem':'test','action':'run'})
            assert ws.receive_json()['type']=='start'
            assert ws.receive_json()['block']['type']=='paper_stage'
            response=client.post('/api/chat/work/pause-test/pause')
            assert response.json()=={'work_status':'paused'}
            event=ws.receive_json()
            assert event['type']=='complete' and event['work_status']=='paused'
            assert work.status=='paused'
            assert any('任务已暂停' in str(args) and 'paper_stage' in str(args) for args in saved)
        work.created_by=9
        assert client.post('/api/chat/work/pause-test/pause').status_code==403
    manager._tasks.clear()


def test_reconnect_after_completion_reloads_saved_history(monkeypatch):
    monkeypatch.setattr(chat,'verify_token',lambda token:7)
    monkeypatch.setattr(chat,'get_db',lambda:iter([SimpleNamespace(close=lambda:None)]))
    monkeypatch.setattr('services.data_services.crud.get_work',lambda *a:SimpleNamespace(created_by=7,status='completed'))
    chat.task_manager._tasks.clear()
    app=FastAPI();app.include_router(chat.router)
    with TestClient(app).websocket_connect('/api/chat/ws/already-done') as ws:
        ws.send_json({'token':'test','task_id':'old-task','last_seq':5})
        assert ws.receive_json()['type']=='auth_success'
        assert ws.receive_json()=={'type':'complete','work_status':'completed','reload_history':True}


@pytest.mark.parametrize('filename,content', [('paper.tex', r'\section{模型与求解} x < y'), ('sales.csv', '日期,销量\n周一,420\n')])
def test_source_and_data_files_are_readable_text(tmp_path,filename,content):
    from services.file_services.workspace_files import WorkspaceFileService
    service = WorkspaceFileService()
    service.base_path = tmp_path
    workspace = tmp_path / 'test-work'
    workspace.mkdir()
    (workspace / filename).write_text(content, encoding='utf-8')
    result = service.read_file('test-work',filename)
    assert result['type'] == 'text' and result['content'] == content
