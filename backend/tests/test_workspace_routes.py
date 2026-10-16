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

@pytest.mark.parametrize('payload', [
    {'type': 'text', 'filename': 'paper.md', 'size': 5, 'content': 'hello'},
    {'type': 'image', 'filename': 'chart.png', 'size': 3, 'content': 'YWJj'},
    {'type': 'binary', 'filename': 'paper.pdf', 'size': 50, 'mime_type': 'application/pdf', 'download_url': '/download/paper.pdf', 'message': 'Binary file - use download button to view'},
])
def test_file_reader_returns_the_service_contract_without_a_second_type_dispatch(client, monkeypatch, payload):
    monkeypatch.setattr(workspace.crud, 'get_work', lambda *args: SimpleNamespace(created_by=7))
    monkeypatch.setattr(workspace.workspace_file_service, 'read_file', lambda *args: payload)
    result = client.get('/api/workspace/example/files/' + payload['filename'])
    assert result.status_code == 200
    assert result.json() == payload

@pytest.mark.parametrize('filename,expected', [('paper.tex', 'text'), ('data.csv', 'text'), ('chart.PNG', 'image'), ('paper.pdf', 'binary'), ('unknown.custom', 'binary')])
def test_file_classification_keeps_the_same_fallback(filename, expected):
    assert workspace.workspace_file_service.detect_file_type(filename) == expected
