import json
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from auth.auth import get_current_user
from database.database import get_db
from routers.work_routes import workspace
from test_experiences import env,submit


@pytest.fixture
def client(env,monkeypatch):
    app=FastAPI();app.include_router(workspace.router);identity={'id':1}
    app.dependency_overrides[get_current_user]=lambda:identity['id']
    def database():
        with env.sessions() as db:yield db
    app.dependency_overrides[get_db]=database
    def scope(wid,db,uid):
        workspace._require_workspace_access(db,wid,uid)
        return env.scope(wid,uid)
    monkeypatch.setattr(workspace,'_experience_scope',scope)
    monkeypatch.setattr(workspace.workspace_file_service,'get_workspace_path',env.root)
    with TestClient(app) as c:yield c,identity


def test_authenticated_experience_list_policy_rebuild_disable_delete_and_usage(env,client):
    c,_=client;a=env.store();submit(a);eid=env.scope().list()['cards'][0]['id']
    base='/api/workspace/B/experiences'
    result=c.get(base);assert result.status_code==200
    assert result.json()['cards'][0]['card']['level']=='verified'
    assert c.put(base+'/policy',json={'enabled':False,'user_id':2}).json()=={'enabled':False}
    assert not c.get(base).json()['enabled']
    assert c.put(base+'/policy',json={'enabled':True}).status_code==200
    assert c.patch(base+'/'+eid,json={'enabled':False}).status_code==200
    assert not c.get(base).json()['cards'][0]['enabled']
    assert c.post(base+'/rebuild').json()['updated']==0
    assert c.delete(base+'/'+eid).status_code==200
    assert c.post(base+'/rebuild').status_code==200
    assert not c.get(base).json()['cards']
    assert c.patch(base+'/'+eid,json={'enabled':True}).status_code==404


@pytest.mark.parametrize('method,suffix,body',[
    ('GET','',None),('PUT','/policy',{'enabled':False}),('POST','/rebuild',None),
    ('PATCH','/guessed',{'enabled':False}),('DELETE','/guessed',None)])
def test_every_endpoint_checks_target_work_owner(client,method,suffix,body):
    c,_=client
    assert c.request(method,'/api/workspace/C/experiences'+suffix,json=body).status_code==403


def test_card_ids_do_not_cross_owners_and_permission_transfer_hides_source(env,client):
    from models.models import Work
    c,identity=client;a=env.store();submit(a);eid=env.scope().list()['cards'][0]['id']
    identity['id']=2
    assert not c.get('/api/workspace/C/experiences').json()['cards']
    assert c.patch('/api/workspace/C/experiences/'+eid,json={'enabled':False}).status_code==404
    assert c.delete('/api/workspace/C/experiences/'+eid).status_code==404
    identity['id']=1
    with env.sessions() as db:db.query(Work).filter(Work.work_id=='A').update({'created_by':2});db.commit()
    assert not c.get('/api/workspace/B/experiences').json()['cards']


def test_api_reports_actual_frozen_usage_and_availability(env,client):
    from ai_system.research.experience import prompt_context
    from ai_system.graph.context import GraphContext
    from test_experiences import TASK,analysis
    c,_=client;a=env.store();submit(a);b=env.store('B');order=b.claim('Q1')
    ctx=GraphContext.create(str(b.root),planner_llm=None,experience_scope=env.scope('B'),experience_store=b,experience_order=order)
    prompt_context(ctx,{'source':TASK,'subproblem':analysis()['subproblems'][0]},['linear_programming'],'design')
    (b.root/'.system/graph_thread.json').write_text(json.dumps({'thread_id':b.research_id}))
    body=c.get('/api/workspace/B/experiences').json()
    assert body['usage'][0]['refs']==order.experience_refs and body['usage'][0]['available']
    eid=order.experience_refs[0]['id'];c.patch('/api/workspace/B/experiences/'+eid,json={'enabled':False})
    assert not c.get('/api/workspace/B/experiences').json()['usage'][0]['available']


def test_experience_schema_migration_upgrades_and_downgrades_existing_database(env):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from models.models import ResearchExperience,ResearchExperiencePreference
    ResearchExperiencePreference.__table__.drop(env.engine)
    ResearchExperience.__table__.drop(env.engine)
    path=Path(__file__).resolve().parents[1]/'alembic/versions/e3a40b1c6d29_add_research_experiences.py'
    spec=importlib.util.spec_from_file_location('experience_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with env.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        schema=inspect(connection)
        assert {'payload','source_key','version','deleted','user_id'}<={c['name'] for c in schema.get_columns('research_experiences')}
        assert any(f['referred_table']=='users' for f in schema.get_foreign_keys('research_experiences'))
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert 'research_experiences' not in inspect(connection).get_table_names()
