"""Versioned recovery with controlled replies. HTTP is forbidden; zero real model calls."""
import asyncio,json,time
from types import SimpleNamespace
import pytest
from test_review_cache import env,no_network
from ai_system.graph.state import ClaimAudit,ConsistencyReview
from ai_system.research.models import Issue,IssueReport,ResearchConflict
from ai_system.research.review_cache import ReviewCache,ReviewSuperseded,run_review,review_context
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
    return env

def model(**kwargs):return SimpleNamespace(**{'model_name':'offline-controlled','calls':0,'gate':None,'error':None,'answer':None,**kwargs})
def item(store,stage='sections'):return next(t for t in tasks.status(store)['tasks'] if t['stage']==stage)
async def drain():
    workers=list(tasks.ACTIVE)
    if workers:await asyncio.wait_for(asyncio.gather(*workers),3)
async def failed(store,stage='sections'):
    with pytest.raises(TimeoutError):await tasks.execute(store,stage,model(error=TimeoutError()))
def edit(store):
    sections={k:v['text'] for k,v in store.snapshot()['sections'].items()};sections['abstract']+='新的措辞。';store.save_sections(sections,[])
def issue_map(store):return {i['id']:i for i in store.snapshot()['issues']}


def test_new_version_only_replaces_old_service_after_actual_completed_review(setup):
    store=setup.store
    async def scenario():
        await failed(store);old=next(i for i in store.snapshot()['issues'] if i['category']=='service')
        writing=store.report_issues([IssueReport(task_id='Q1',category='writing',section='evaluation',message='保留实质写作意见')],'manual')[0]
        science=store.report_issues([IssueReport(task_id='Q1',category='model',message='保留科研问题')],'local_review')[0]
        edit(store);t=item(store);assert t['state']=='pending' and issue_map(store)[old['id']]['status']=='confirmed'
        llm=model();llm.gate=asyncio.Event();r=tasks.start(store,'sections',t['version'],'first',llm)
        duplicate=tasks.start(store,'sections',t['version'],'second',llm)
        assert duplicate['id']==r['id'] and duplicate['deduplicated']
        await asyncio.sleep(.01);assert llm.calls==1 and item(store)['state']=='running'
        assert issue_map(store)[old['id']]['status']=='confirmed'
        llm.gate.set();await drain()
        assert item(store)['state']=='completed'
        issues=issue_map(store);assert issues[old['id']]['status']=='superseded'
        assert issues[old['id']]['review_supersession']['old_review_id']==old['review_scope']['failed_review_id']
        assert issues[writing['id']]['status']=='confirmed' and issues[science['id']]['status']=='suspected'
        history=tasks.status(store)['history'];assert any(h['original_state']=='failed' and h['replacement_review_id'] for h in history)
        assert not store.delivery_ready(store.snapshot())
        assert ResearchRuntime(store.root).status()['used']==0
    asyncio.run(scenario())


def test_same_version_recovers_instead_of_superseding_and_retains_failure(setup):
    store=setup.store
    async def scenario():
        await failed(store);old=next(i for i in store.snapshot()['issues'] if i['category']=='service')
        t=item(store);assert t['state']=='failed'
        receipt=tasks.start(store,'sections',t['version'],'once',model());await drain()
        issue=issue_map(store)[old['id']];assert issue['status']=='resolved'
        assert issue['review_resolution']['failed_review_id']==old['review_scope']['failed_review_id']
        previous=next(r for r in tasks.records(store) if r['id']==old['review_scope']['failed_review_id']);assert previous['state']=='failed' and previous['error_code']=='TimeoutError'
        assert tasks.start(store,'sections',t['version'],'once',model())['id']==receipt['id']
    asyncio.run(scenario())


def test_new_review_failure_never_clears_old_failures(setup):
    store=setup.store
    async def scenario():
        await failed(store);old=next(i for i in store.snapshot()['issues'] if i['category']=='service');edit(store)
        t=item(store);tasks.start(store,'sections',t['version'],'fail-again',model(error=TimeoutError()));await drain()
        assert item(store)['state']=='failed' and issue_map(store)[old['id']]['status']=='confirmed'
        assert not any(r.get('superseded_by') for r in tasks.records(store))
    asyncio.run(scenario())


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


def test_fragment_cannot_supersede_full_chapter_service(setup):
    store=setup.store
    async def scenario():
        await failed(store);old=next(i for i in store.snapshot()['issues'] if i['category']=='service')
        context=review_context(setup.ctx,setup.state,'fragment','system','prompt',ClaimAudit,sections=setup.state['sections'],extra={'non_blocking':True})
        cache=ReviewCache(store);r=cache.start('fragment',context);cache.complete(r,{'verdict':'equivalent','reason':'fixture'})
        assert issue_map(store)[old['id']]['status']=='confirmed' and item(store)['state']=='failed'
    asyncio.run(scenario())


def test_substantive_issue_cannot_be_marked_superseded():
    with pytest.raises(ValueError):Issue.model_validate({'id':'I1','task_id':'Q1','category':'writing','message':'问题','status':'superseded','source':'test'})


def test_api_owner_research_and_budget_guards(setup,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.work_routes import workspace
    from auth.auth import get_current_user
    from database.database import get_db
    store=setup.store
    (store.root/'.system/graph_thread.json').write_text(json.dumps({'thread_id':store.research_id,'engine_version':'research-v3'}))
    app=FastAPI();app.include_router(workspace.router)
    app.dependency_overrides[get_current_user]=lambda:7
    app.dependency_overrides[get_db]=lambda:object()
    monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(created_by=7,output_mode='markdown'))
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
        monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(created_by=8))
        assert client.get('/api/workspace/work/review-tasks').status_code==403
        assert client.post('/api/workspace/work/review-tasks',json=body).status_code==403
    assert rt.status()['used']==0


def test_recovery_intent_requires_actual_same_document_proof(setup):
    store=setup.store;cache=ReviewCache(store)
    intent=cache.start('sections',tasks.intent_context(store,'sections'),'recovery-intent')
    ctx=review_context(setup.ctx,setup.state,'sections','wrong','wrong',ClaimAudit,sections={'abstract':'different'})
    child=cache.start('sections',ctx);cache.complete(child,{'issues':[]})
    with pytest.raises(ValueError,match='实际完成审查'):cache.complete(intent,{'outcome':'passed'},provenance={'verified_records':[child['id']]})
    assert next(r for r in tasks.records(store) if r['id']==intent['id'])['state']=='running'


def test_failed_publication_of_review_findings_is_not_success(setup,monkeypatch):
    store=setup.store
    def fail(*args):raise OSError('controlled persistence failure')
    monkeypatch.setattr(tasks,'_save_findings',fail)
    async def scenario():
        tasks.start(store,'sections',item(store)['version'],'metadata-failure',model());await drain()
        assert any(r['state']=='completed' and r['source']!='recovery-intent' for r in tasks.records(store))
        assert item(store)['state']=='failed' and item(store)['can_resume']
        assert not store.delivery_ready(store.snapshot())
    asyncio.run(scenario())


def test_manual_source_conflict_never_claims_current_file_reviewed(setup):
    store=setup.store;store.publish_paper('original source')
    async def scenario():
        tasks.start(store,'sections',item(store)['version'],'completed',model());await drain()
        assert item(store)['state']=='completed'
    asyncio.run(scenario())
    (store.root/'paper.md').write_text('manual changes outside saved sections')
    t=item(store);assert t['state']=='pending' and not t['can_resume'] and '合并原稿' in t['reason']
