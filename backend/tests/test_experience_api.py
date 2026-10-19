"""Personal library ownership, lifecycle and project usage are independent routes."""
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from auth.auth import get_current_user
from database.database import get_db
from routers.work_routes import workspace
from routers import experience_routes
from test_experiences import env, submit, adopt_ready


@pytest.fixture
def client(env,monkeypatch):
    app=FastAPI();app.include_router(workspace.router);app.include_router(experience_routes.router);identity={'id':1}
    app.dependency_overrides[get_current_user]=lambda:identity['id']
    def database():
        with env.sessions() as db:yield db
    app.dependency_overrides[get_db]=database
    monkeypatch.setattr(experience_routes,'ExperienceScope',lambda uid:env.scope(None,uid))
    def scope(wid,db,uid):
        workspace._require_workspace_access(db,wid,uid)
        return env.scope(wid,uid)
    monkeypatch.setattr(workspace,'_experience_scope',scope)
    monkeypatch.setattr(workspace.workspace_file_service,'get_workspace_path',env.root)
    with TestClient(app) as c:yield c,identity


def test_personal_library_lifecycle_and_no_project_scoped_crud(env,client):
    c,_=client;submit(env.store());card=env.scope().list()['cards'][0];eid=card['id'];base='/api/experiences'
    assert c.get(base).json()['cards'][0]['status']=='candidate'
    assert c.post(f'{base}/{eid}/transitions',json={'action':'adopt','base_version':1}).status_code==409
    for action in ('validate','adopt','withdraw'):
        assert c.post(f'{base}/{eid}/transitions',json={'action':action,'base_version':1}).status_code==200
    assert c.get(base).json()['cards'][0]['status']=='withdrawn'
    assert c.get(base+'?q=does-not-exist').json()['cards']==[]
    assert c.put(base+'/policy',json={'enabled':False,'user_id':2}).status_code==422
    assert c.put(base+'/policy',json={'enabled':False}).json()=={'enabled':False}
    assert c.patch(base+'/'+eid,json={'enabled':False}).status_code==200
    assert c.post(base+'/rebuild').json()['updated']==0
    assert c.delete(base+'/'+eid).status_code==200
    assert not c.get(base).json()['cards']
    assert c.post(base+'/rebuild').status_code==200
    assert not c.get(base).json()['cards']
    assert c.get('/api/workspace/B/experiences').status_code==404


def test_personal_ids_cannot_cross_users_and_usage_checks_project_owner(env,client):
    c,identity=client;submit(env.store());eid=env.scope().list()['cards'][0]['id'];identity['id']=2
    assert not c.get('/api/experiences').json()['cards']
    assert c.patch('/api/experiences/'+eid,json={'enabled':False}).status_code==404
    assert c.delete('/api/experiences/'+eid).status_code==404
    assert c.post('/api/experiences/'+eid+'/transitions',json={'action':'validate','base_version':1}).status_code==404
    assert c.get('/api/workspace/B/experience-usage').status_code==403
    identity['id']=1
    from models.models import Project
    with env.sessions() as db:db.query(Project).filter(Project.work_id=='A').update({'created_by':2});db.commit()
    assert not c.get('/api/experiences').json()['cards']


def test_api_exposes_actual_frozen_versions_and_revocation(env,client):
    from ai_system.research.experience import prompt_context
    from ai_system.graph.context import GraphContext
    from test_experiences import TASK,analysis
    c,_=client;submit(env.store());adopt_ready(env.scope());b=env.store('B');order=b.claim('Q1')
    ctx=GraphContext.create(str(b.root),planner_llm=None,experience_scope=env.scope('B'),experience_store=b,experience_order=order)
    prompt_context(ctx,{'source':TASK,'subproblem':analysis()['subproblems'][0]},['linear_programming'],'design')
    (b.root/'.system/graph_thread.json').write_text(json.dumps({'thread_id':b.research_id}))
    body=c.get('/api/workspace/B/experience-usage').json()
    assert body['usage'][0]['refs']==order.experience_refs and body['usage'][0]['available']
    original_title=body['usage'][0]['sources'][0]['title']
    assert body['usage'][0]['sources'][0]['source_work_id']=='A'
    eid=order.experience_refs[0]['id'];c.post('/api/experiences/'+eid+'/transitions',json={'action':'withdraw','base_version':1})
    body=c.get('/api/workspace/B/experience-usage').json()
    assert not body['usage'][0]['available'] and body['usage'][0]['refs']==order.experience_refs
    from models.models import ResearchExperience
    from ai_system.research.store import digest
    with env.sessions() as db:
        row=db.get(ResearchExperience,eid)
        row.payload={**row.payload,'question':'这是个人库中新版本的内容'}
        row.version+=1;row.content_hash=digest(row.payload);db.commit()
    source=c.get('/api/workspace/B/experience-usage').json()['usage'][0]['sources'][0]
    assert source['title']==original_title and source['version']==1
    assert c.delete('/api/experiences/'+eid).status_code==200
    body=c.get('/api/workspace/B/experience-usage').json()
    assert body['usage'][0]['refs']==order.experience_refs
    assert body['usage'][0]['sources'][0]['title']=='已删除的经验'
    assert body['usage'][0]['sources'][0]['source_work_id'] is None
