"""Deactivation revokes password access, HTTP tokens and live subscriptions."""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from auth.auth import create_access_token, get_password_hash
from database.database import get_db
from models.models import Base, Project, User
from routers import project_route_access
from routers.auth_routes.auth import router as auth_router
from routers.chat_routes import chat


@pytest.fixture
def app_client(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(User(id=1, username='active-user', email='active@example.com',
                    password_hash=get_password_hash('secret12'), is_active=True))
        db.add(Project(work_id='test-work', title='Test', created_by=1, architecture_version=2))
        db.commit()

    def database():
        with sessions() as db:
            yield db

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(chat.router)

    @app.get('/test/{work_id}', dependencies=[Depends(project_route_access)])
    def project_endpoint(work_id: str):
        return {'work_id': work_id}

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(chat, 'get_db', database)
    token = create_access_token({'user_id': 1})
    with TestClient(app) as client:
        yield client, sessions, token
    engine.dispose()


def deactivate(sessions):
    with sessions() as db:
        db.query(User).filter_by(id=1).update({'is_active': False})
        db.commit()


def test_deactivated_account_cannot_login_or_use_existing_http_token(app_client):
    client, sessions, token = app_client
    headers = {'Authorization': f'Bearer {token}'}
    credentials = {'email': 'active@example.com', 'password': 'secret12'}
    assert client.post('/auth/login', json=credentials).status_code == 200
    assert client.get('/auth/me', headers=headers).status_code == 200
    assert client.get('/test/test-work', headers=headers).status_code == 200

    deactivate(sessions)
    assert client.post('/auth/login', json=credentials).status_code == 401
    assert client.get('/auth/me', headers=headers).status_code == 401
    assert client.get('/test/test-work', headers=headers).status_code == 401
    missing_headers = {'Authorization': f"Bearer {create_access_token({'user_id': 999})}"}
    assert client.get('/auth/me', headers=missing_headers).status_code == 401
    assert client.get('/test/test-work', headers=missing_headers).status_code == 401


def test_deactivated_account_cannot_open_or_command_websocket(app_client):
    client, sessions, token = app_client
    with client.websocket_connect('/api/chat/ws/test-work') as ws:
        ws.send_json({'token': token})
        assert ws.receive_json()['type'] == 'auth_success'
        deactivate(sessions)
        ws.send_json({'type': 'ping'})
        with pytest.raises(WebSocketDisconnect) as disconnected:
            ws.receive_json()
        assert disconnected.value.code == 1008

    with client.websocket_connect('/api/chat/ws/test-work') as ws:
        ws.send_json({'token': token})
        assert ws.receive_json()['type'] == 'error'
        with pytest.raises(WebSocketDisconnect) as disconnected:
            ws.receive_json()
        assert disconnected.value.code == 1008


def test_deactivated_account_cannot_receive_broadcast(app_client):
    client, sessions, token = app_client
    with client.websocket_connect('/api/chat/ws/test-work') as ws:
        ws.send_json({'token': token})
        assert ws.receive_json()['type'] == 'auth_success'
        deactivate(sessions)
        client.portal.call(chat.manager.send, 'test-work', {'type': 'content', 'content': 'private'})
        with pytest.raises(WebSocketDisconnect) as disconnected:
            ws.receive_json()
        assert disconnected.value.code == 1008
