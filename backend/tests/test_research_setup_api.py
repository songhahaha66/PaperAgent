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
from test_experiences import env


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


def test_setup_and_overview_before_run_and_cannot_overwrite_checkpoint(env,client):
    base='/api/workspace/B'
    result=client.put(base+'/research-setup',json={'request_limit':40,'compact_paper':True,'skill_keys':['regression']})
    assert result.status_code==200,result.text
    overview=client.get(base+'/research-settings').json()
    assert overview['budget']['used']==0 and overview['settings']['request_limit']==40
    assert 'research' not in overview
    root=env.root('B');(root/'.system/graph_thread.json').write_text(json.dumps({'thread_id':'existing'}))
    assert client.put(base+'/research-setup',json={'request_limit':1}).status_code==409
    assert client.get(base+'/research-settings').json()['settings']['request_limit']==40


def test_template_scaffold_is_not_a_published_research_artifact(env,client):
    root=env.root('B');(root/'.system').mkdir(parents=True,exist_ok=True)
    (root/'.system/graph_thread.json').write_text(json.dumps({'thread_id':'preparing','engine_version':'research-v3'}))
    (root/'paper.tex').write_text('template scaffold')
    overview=client.get('/api/workspace/B/research-settings')
    assert overview.status_code==200
    from services.chat_services.research_session import research_snapshot
    assert research_snapshot(root,'B','created').artifacts==[]
    # Draft reading uses the project endpoint independently of formal outcomes.
    draft=client.get('/api/workspace/B/paper/project')
    assert draft.status_code==200,draft.text
    assert draft.json()['main']=='paper.tex'
    assert next(f for f in draft.json()['files'] if f['path']=='paper.tex')['text']=='template scaffold'
    assert research_snapshot(root,'B','created').artifacts==[]


@pytest.mark.parametrize('method,path,body',[('PUT','research-setup',{}),('GET','research-settings',None),
    ('GET','research/issues/unknown/execution-recovery',None),
    ('POST','research/issues/unknown/execution-recovery',{'proof_hash':'0'*64})])
def test_unified_routes_keep_work_owner_boundary(client,method,path,body):
    assert client.request(method,'/api/workspace/C/'+path,json=body).status_code==403


def test_config_limits_and_inflight_changes_are_rejected(client,monkeypatch):
    assert client.put('/api/workspace/B/research-setup',json={'request_limit':0}).status_code==422
    assert client.put('/api/workspace/B/research-setup',json={'request_limit':41}).status_code==200
    monkeypatch.setattr(workspace.task_manager,'get_running_task',lambda _:object())
    assert client.put('/api/workspace/B/research-setup',json={}).status_code==409


def test_readiness_uses_runtime_env_fallback_and_exposes_only_booleans(env,monkeypatch):
    from routers.config_routes.model_config import router
    monkeypatch.setenv('PAPERAGENT_API_KEY','offline-test-secret')
    monkeypatch.setenv('PAPERAGENT_API_MODEL','offline-model')
    monkeypatch.setenv('PAPERAGENT_API_BASE','https://example.test/v1')
    app=FastAPI();app.include_router(router)
    app.dependency_overrides[get_current_user]=lambda:1
    def database():
        with env.sessions() as db:yield db
    app.dependency_overrides[get_db]=database
    with TestClient(app) as c:
        result=c.get('/model-configs/readiness')
    assert result.status_code==200
    assert result.json()=={'ready':True,'roles':{'brain':True,'code':True,'writing':True}}
    assert 'offline-test-secret' not in result.text


def test_new_default_is_metered_without_implicit_cap_and_permissions_unchanged(client):
    response=client.put('/api/workspace/B/research-setup',json={'confirm_models':True,'literature_mode':'off'})
    assert response.status_code==200,response.text
    data=client.get('/api/workspace/B/research-settings').json()
    assert data['budget']['request_limit'] is None and data['settings']['timeout_seconds'] is None
    assert data['settings']['confirm_models'] is True and data['modules']['literature']['mode']=='off'
    assert data['settings']['limit_source']=='default'
