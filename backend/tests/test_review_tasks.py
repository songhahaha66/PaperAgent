"""Versioned recovery with controlled replies. HTTP is forbidden; zero real model calls."""
from services.file_services.workspace_layout import workspace_path

import asyncio,json,time
from types import SimpleNamespace
import pytest
from helpers.review import env,no_network
from ai_system.graph.state import ClaimAudit,ConsistencyReview
from ai_system.research.models import ResearchConflict
from ai_system.research.review_cache import ReviewCache, ReviewSuperseded
from ai_system.research.runtime import ResearchRuntime
from services.chat_services import review_tasks as tasks

@pytest.fixture
def setup(env,monkeypatch):
    env.store.save_sections(env.state['sections'],[])
    async def controlled(llm,schema,messages,validate=None,**kwargs):
        llm.calls+=1
        if llm.gate:await llm.gate.wait()
        if llm.error:raise llm.error
        answer=llm.answer or (ClaimAudit(issues=[]) if schema is ClaimAudit else ConsistencyReview(conflicts=[]))
        if validate:validate(answer)
        return answer
    monkeypatch.setattr(tasks,'call_structured',controlled)
    from ai_system.modules.writing import consistency
    monkeypatch.setattr(consistency,'call_structured',controlled)
    return env

def model(**kwargs):return SimpleNamespace(**{'model_name':'offline-controlled','calls':0,'gate':None,'error':None,'answer':None,**kwargs})
def item(store,stage='sections'):return next(t for t in tasks.status(store)['tasks'] if t['stage']==stage)
async def drain():
    workers=list(tasks.ACTIVE)
    if workers:await asyncio.wait_for(asyncio.gather(*workers),3)
async def failed(store,stage='sections'):
    llm=model(error=TimeoutError('controlled timeout'))
    with pytest.raises(RuntimeError if stage=='sections' else TimeoutError,match='未完成|controlled timeout'):
        await tasks.execute(store,stage,llm)
    assert llm.calls==1
    failure=next(r for r in tasks.records(store) if r['stage']==stage and r['state']=='failed')
    assert failure['error_code']=='TimeoutError'
    assert failure['diagnostic']['reason']=='TimeoutError'
    assert 'controlled timeout' not in str(failure['diagnostic']), 'provider exception text must stay private'
def edit(store):
    sections={k:v['text'] for k,v in store.snapshot()['sections'].items()};sections['abstract']+='新的措辞。';store.save_sections(sections,[])
def test_late_old_response_cannot_overwrite_new_version_or_resolve_services(setup):
    store=setup.store
    async def scenario():
        old_llm=model();old_llm.gate=asyncio.Event();old_t=item(store)
        old=tasks.start(store,'sections',old_t['version'],'old',old_llm);await asyncio.sleep(.01)
        edit(store);new_t=item(store);assert new_t['version']!=old_t['version']
        fresh=tasks.start(store,'sections',new_t['version'],'new',model())
        await asyncio.sleep(.03)
        old_llm.gate.set();await drain()
        old_row=next(r for r in tasks.records(store) if r['id']==old['id']);assert old_row['state']=='superseded'
        assert any(r['id']==fresh['id'] and r['state']=='completed' for r in tasks.records(store))
        assert item(store)['state']=='completed'
        assert not any(r.get('late_response') and r['state']=='completed' for r in tasks.records(store))
    asyncio.run(scenario())


def test_budget_and_stale_version_preflight_do_not_construct_model_or_reset_usage(setup):
    store=setup.store;rt=ResearchRuntime(store.root);rt.configure({'request_limit':1,'timeout_seconds':90});rt.reserve('offline.invalid')
    t=item(store);assert not t['can_resume'] and '预算不足' in t['reason']
    def forbidden():raise AssertionError('Model construction is forbidden after preflight rejection')
    with pytest.raises(ResearchConflict,match='预算不足'):tasks.start(store,'sections',t['version'],'no-budget',model_factory=forbidden)
    with pytest.raises(ResearchConflict,match='版本'):tasks.start(store,'sections','f'*64,'stale',model_factory=forbidden)
    assert rt.status()['used']==1 and len(rt.requests())==1


def test_orphaned_lease_recovers_without_accepting_late_owner(setup):
    store=setup.store;cache=ReviewCache(store);ctx=tasks.intent_context(store,'sections');r=cache.start('sections',ctx,'recovery-intent')
    r['lease']['expires_at']=time.time()-1
    with store._transaction() as db:db.execute('UPDATE review_records SET payload=? WHERE id=?',(json.dumps(r),r['id']))
    assert item(store)['state']=='failed'
    with pytest.raises(ReviewSuperseded):cache.complete(r,{'outcome':'passed'})
    async def scenario():
        tasks.start(store,'sections',item(store)['version'],'after-restart',model());await drain()
        assert item(store)['state']=='completed'
    asyncio.run(scenario())


def test_cancellation_persists_recoverable_failure(setup):
    store=setup.store
    async def scenario():
        llm=model();llm.gate=asyncio.Event();tasks.start(store,'sections',item(store)['version'],'cancel',llm)
        await asyncio.sleep(.01);workers=list(tasks.ACTIVE)
        for w in workers:w.cancel()
        await asyncio.gather(*workers,return_exceptions=True)
        assert item(store)['state']=='failed'
        assert not any(r['state']=='completed' for r in tasks.records(store))
        assert any(r.get('diagnostic',{}).get('recoverable') for r in tasks.records(store))
    asyncio.run(scenario())


def test_api_owner_research_and_budget_guards(setup,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.work_routes import workspace
    from auth.auth import get_current_user
    from database.database import get_db
    store=setup.store
    (workspace_path(store.root, '.system/graph_thread.json')).write_text(json.dumps({'thread_id':store.research_id,'engine_version':'research-v3'}))
    app=FastAPI();app.include_router(workspace.router)
    app.dependency_overrides[get_current_user]=lambda:7
    app.dependency_overrides[get_db]=lambda:object()
    monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(architecture_version=2, created_by=7,output_mode='markdown'))
    monkeypatch.setattr(workspace.workspace_file_service,'get_workspace_path',lambda *a:store.root)
    rt=ResearchRuntime(store.root);rt.configure({'request_limit':0,'timeout_seconds':90})
    def forbidden(*a):raise AssertionError('No model construction for unauthorized or exhausted request')
    monkeypatch.setattr(workspace,'_paper_writing_model',forbidden)
    with TestClient(app) as client:
        response=client.get('/api/workspace/work/review-tasks');assert response.status_code==200
        t=next(t for t in response.json()['tasks'] if t['stage']=='sections')
        body={'research_id':store.research_id,'stage':'sections','version':t['version'],'command_id':'retry'}
        assert client.post('/api/workspace/work/review-tasks',json=body).status_code==409
        assert client.post('/api/workspace/work/review-tasks',json={**body,'research_id':'other'}).status_code==409
        monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(architecture_version=2, created_by=8))
        assert client.get('/api/workspace/work/review-tasks').status_code==403
        assert client.post('/api/workspace/work/review-tasks',json=body).status_code==403
    assert rt.status()['used']==0
