"""Personal library ownership, lifecycle and project usage are independent routes."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from auth.auth import get_current_user
from database.database import get_db
from routers.work_routes import workspace
from routers import experience_routes
from helpers.experience import env, submit


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
