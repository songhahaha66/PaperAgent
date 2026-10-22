"""Workspace cleanup must preserve ownership gates and file response contracts."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth.auth import get_current_user
from database.database import get_db
from routers.work_routes import workspace

REQUESTS = [
    ('GET', '/files', {}),
    ('GET', '/paper', {}),
    ('GET', '/files/paper.md', {}),
    ('GET', '/download/paper.pdf', {}),
    ('GET', '/images/chart.png', {}),
    ('POST', '/files/paper.md', {'data': {'content': 'new'}}),
    ('POST', '/upload', {'data': {'file_path': 'data.csv'}, 'files': {'file': ('data.csv', b'a,b\n1,2\n')}}),
    ('DELETE', '/files/paper.md', {}),
    ('POST', '/mkdir', {'data': {'dir_path': 'results'}}),
    ('GET', '/export', {}),
]

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(workspace.router)
    app.dependency_overrides[get_current_user] = lambda: 7
    app.dependency_overrides[get_db] = lambda: object()
    with TestClient(app) as test_client:
        yield test_client

@pytest.mark.parametrize('method,path,kwargs', REQUESTS)
@pytest.mark.parametrize('owner,expected', [(9, 403), (None, 404)])
def test_all_workspace_operations_preserve_ownership(client, monkeypatch, method, path, kwargs, owner, expected):
    monkeypatch.setattr(workspace.crud, 'get_work', lambda *args: SimpleNamespace(created_by=owner) if owner is not None else None)
    assert client.request(method, '/api/workspace/example' + path, **kwargs).status_code == expected

def test_empty_paper_api_is_readable_before_research(client, monkeypatch, tmp_path):
    monkeypatch.setattr(workspace.crud, 'get_work', lambda *args: SimpleNamespace(created_by=7, architecture_version=2))
    monkeypatch.setattr(workspace.workspace_file_service, 'get_workspace_path', lambda _: tmp_path)
    response = client.get('/api/workspace/example/paper')
    assert response.status_code == 200
    assert response.json()['sections'] == [] and response.json()['artifacts'] == []
    assert list(tmp_path.iterdir()) == []
