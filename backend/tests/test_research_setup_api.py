from services.file_services.workspace_layout import workspace_path
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from auth.auth import get_current_user
from database.database import get_db
from routers.work_routes import workspace
from helpers.experience import env


@pytest.fixture
def client(env,monkeypatch):
    import services.research_experience as experiences
    original=experiences.ExperienceScope
    monkeypatch.setattr(experiences,'ExperienceScope',lambda uid,wid:original(uid,wid,env.sessions,env.root))
    monkeypatch.setattr(workspace.workspace_file_service,'get_workspace_path',env.root)
    app=FastAPI();app.include_router(workspace.router)
    app.dependency_overrides[get_current_user]=lambda:1
    def database():
        with env.sessions() as db:yield db
    app.dependency_overrides[get_db]=database
    with TestClient(app) as c:yield c


def test_setup_before_run_cannot_overwrite_checkpoint(env,client):
    base='/api/workspace/B'
    result=client.put(base+'/research-setup',json={'request_limit':40,'compact_paper':True,'skill_keys':['regression']})
    assert result.status_code==200,result.text
    from ai_system.research.runtime import ResearchRuntime
    runtime=ResearchRuntime(env.root('B'))
    assert runtime.status()['used']==0 and runtime.policy()['request_limit']==40
    root=env.root('B');(workspace_path(root, '.system/graph_thread.json')).write_text(json.dumps({'thread_id':'existing'}))
    assert client.put(base+'/research-setup',json={'request_limit':1}).status_code==409
    assert runtime.policy()['request_limit']==40


@pytest.mark.parametrize('method,path,body',[('PUT','research-setup',{}),
    ('GET','paper/project',None),
    ('POST','paper/executions/unknown/resume',{'research_id':'unknown'})])
def test_unified_routes_keep_work_owner_boundary(client,method,path,body):
    assert client.request(method,'/api/workspace/C/'+path,json=body).status_code==403


def test_config_limits_and_inflight_changes_are_rejected(client,monkeypatch):
    assert client.put('/api/workspace/B/research-setup',json={'request_limit':0}).status_code==422
    assert client.put('/api/workspace/B/research-setup',json={'request_limit':41}).status_code==200
    monkeypatch.setattr(workspace.task_manager,'get_running_task',lambda _:object())
    assert client.put('/api/workspace/B/research-setup',json={}).status_code==409
