"""Offline review-cache tests. Any real HTTP send fails this module immediately."""
import asyncio,copy,json,sys
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.research.store import ResearchStore,digest
from ai_system.research.models import WorkerResult,ReviewRecord,IssueReport
from ai_system.research.validation import report_fingerprint
from ai_system.research.review_cache import ReviewCache,run_review,review_context
from ai_system.research.review_migration import context_from_store,completed_reconcile_proof,section_failure_proof
from ai_system.graph.state import ConsistencyReview,Conflict,ClaimAudit


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args,**kwargs):raise AssertionError('Real HTTP is forbidden in offline review tests')
    async def refuse_async(*args,**kwargs):raise AssertionError('Real HTTP is forbidden in offline review tests')
    monkeypatch.setattr(httpx.Client,'send',refuse);monkeypatch.setattr(httpx.AsyncClient,'send',refuse_async)


@pytest.fixture
def env(tmp_path):
    store=ResearchStore(tmp_path,'reviews');(tmp_path/'data').mkdir();(tmp_path/'data/input.csv').write_text('x\n1\n')
    source='检查score是有限数。'
    analysis={'subproblems':[{'id':'Q1','title':'数值验证','question':source,'depends_on':[]}],
        'requirements':[{'id':'RQ1','kind':'constraint','statement':source,'source_quote':source,'task_ids':['Q1'],
            'checks':[{'id':'finite','description':'有限','left':{'task_id':'Q1','name':'score'},'operator':'finite'}]}],'assumptions':[]}
    store.initialize(source,analysis);order=store.claim('Q1');run=tmp_path/'runs/one';run.mkdir(parents=True)
    (run/'input.py').write_text('print(1.0,2.0)');(run/'stdout.log').write_text('1.0 2.0\n')
    (tmp_path/'manifest.json').write_text(json.dumps({'runs':{'one':{'status':'success','input':'runs/one/input.py','stdout':'runs/one/stdout.log'}}}))
    results=[{'id':'R'+str(i+1),'subproblem':'Q1','kind':'number','name':name,'value':str(value),'run_id':'one','path':'','description':'','unit':''} for i,(name,value) in enumerate([('score',1.0),('baseline',2.0)])]
    claims=[{'id':'C'+str(i+1),'subproblem':'Q1','statement':'结论 {{R:R'+str(i+1)+'}}','strength':'倾向','evidence':['R'+str(i+1)]} for i in range(2)]
    report={'status':'completed','summary':'完成','requirement_evidence':[{'requirement_id':'RQ1','evidence_refs':['R1']}]}
    store.commit(WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',results=results,claims=claims,report=report,
        reviews=[ReviewRecord(report_hash=report_fingerprint(report),issues=[])]))
    snapshot=store.snapshot();state={'analysis':{'subproblems':[t['subproblem'] for t in snapshot['tasks'].values()],
        'requirements':snapshot['spec']['requirements'],'assumptions':[]},'solutions':{'Q1':snapshot['tasks']['Q1']['report']},
        'sections':{'abstract':'摘要正文。'*30,'evaluation':'证据局限。'*30},'consistency':[]}
    ctx=context_from_store(store,'offline-model');return SimpleNamespace(store=store,state=state,ctx=ctx)


def invoke(env,perform,stage='reconcile',sections=None):
    schema=ClaimAudit if stage=='sections' else ConsistencyReview
    return asyncio.run(run_review(env.ctx,env.state,stage,schema,'system','prompt',perform,sections=sections))


def test_completed_review_is_reused_without_losing_findings_or_spending_again(env):
    calls=[]
    async def review():calls.append(1);return ConsistencyReview(conflicts=[Conflict(claims=['C1'],issue='措辞与差值方向不一致',resolution='保留实测值并统一方向')])
    before=env.store.snapshot()['commit_seq'];first=invoke(env,review);second=invoke(env,review)
    assert first==second and len(second.conflicts)==1 and calls==[1]
    assert env.store.snapshot()['commit_seq']==before  # Metadata cannot invalidate paper-edit optimistic versions.


@pytest.mark.parametrize('change',['input','contract','evidence','source_file'])
def test_relevant_context_change_never_reuses_old_review(env,change):
    calls=[]
    async def review():calls.append(1);return ConsistencyReview(conflicts=[])
    invoke(env,review)
    if change=='input':(env.store.root/'data/input.csv').write_text('x\n2\n')
    elif change=='source_file':(env.store.root/'runs/one/stdout.log').write_text('changed execution output')
    elif change=='evidence':env.ctx.registry.results['R1'].value='1.1'
    else:
        with env.store._transaction() as db:
            state=env.store._read(db);state['spec']['requirements'][0]['statement']='changed contract';env.store._save(db,state,'test_changed',{})
    invoke(env,review);assert calls==[1,1]


def test_chapter_edit_invalidates_section_audit_but_not_unrelated_reconcile(env):
    calls=[]
    async def audit():calls.append(1);return ClaimAudit(issues=[])
    invoke(env,audit,'sections',env.state['sections']);env.state['sections']['abstract']+='Changed'
    invoke(env,audit,'sections',env.state['sections']);assert len(calls)==2


def test_failure_is_not_success_and_only_matching_service_issue_closes(env):
    async def failure():raise TimeoutError()
    with pytest.raises(TimeoutError):invoke(env,failure)
    cache=ReviewCache(env.store);context=review_context(env.ctx,env.state,'reconcile','system','prompt',ConsistencyReview)
    assert cache.get('reconcile',context,ConsistencyReview) is None
    own=next(i for i in env.store.snapshot()['issues'] if i.get('review_scope'))
    unrelated=env.store.report_issues([IssueReport(task_id='Q1',category='service',message='different service failed')],'other')[0]
    citation=env.store.report_issues([IssueReport(task_id='Q1',category='writing',section='solve:Q1',message='C32 remains unverified')],'sections')[0]
    scientific=env.store.report_issues([IssueReport(task_id='Q1',category='model',message='unresolved scientific finding')],'local_review')[0]
    async def success():return ConsistencyReview(conflicts=[])
    invoke(env,success)
    state=env.store.snapshot();issues={i['id']:i for i in state['issues']}
    assert issues[own['id']]['status']=='resolved' and issues[own['id']]['review_resolution']['context_hash']==digest(context)
    assert issues[unrelated['id']]['status']=='confirmed' and issues[citation['id']]['status']=='confirmed'
    assert issues[scientific['id']]['status']=='suspected'
    assert not env.store.delivery_ready(state)


def test_only_completed_same_coverage_new_version_supersedes_old_service_failure(env):
    async def failure():raise TimeoutError()
    with pytest.raises(TimeoutError):invoke(env,failure)
    original=env.store.snapshot()['issues'][0]
    async def sections():return ClaimAudit(issues=[])
    invoke(env,sections,'sections',env.state['sections'])
    assert env.store.snapshot()['issues'][0]['status']=='confirmed'
    (env.store.root/'data/input.csv').write_text('changed')
    async def success():return ConsistencyReview(conflicts=[])
    invoke(env,success)
    replacement=next(i for i in env.store.snapshot()['issues'] if i['id']==original['id'])
    assert replacement['status']=='superseded' and replacement['review_supersession']['old_review_id']==original['review_scope']['failed_review_id']


def test_default_empty_response_and_corrupt_cache_are_not_success(env):
    async def empty():return ConsistencyReview()  # Simulates a missing verdict field.
    with pytest.raises(ValueError,match='missing_verdict'):invoke(env,empty)
    async def valid():return ConsistencyReview(conflicts=[])
    invoke(env,valid)
    with env.store._transaction() as db:
        row=db.execute("SELECT id,payload FROM review_records WHERE state='completed'").fetchone();record=json.loads(row[1]);record['result']={'conflicts':[{'claims':['invented']}]}
        db.execute('UPDATE review_records SET payload=? WHERE id=?',(json.dumps(record),row[0]))
    context=review_context(env.ctx,env.state,'reconcile','system','prompt',ConsistencyReview)
    assert ReviewCache(env.store).get('reconcile',context,ConsistencyReview) is None


def test_context_change_during_response_does_not_cache_or_close(env):
    async def changed():
        (env.store.root/'data/input.csv').write_text('changed during request')
        return ConsistencyReview(conflicts=[])
    from ai_system.research.review_cache import ReviewSuperseded
    with pytest.raises(ReviewSuperseded):invoke(env,changed)
    with env.store._connect() as db:
        assert not db.execute("SELECT 1 FROM review_records WHERE state='completed'").fetchone()
        row=db.execute("SELECT payload FROM review_records WHERE state='superseded'").fetchone()
        assert row and json.loads(row[0])['late_response']


def test_delivery_metadata_does_not_repeat_unchanged_review(env):
    calls=[]
    async def review():calls.append(1);return ConsistencyReview(conflicts=[])
    invoke(env,review)
    with env.store._transaction() as db:
        state=env.store._read(db);state['delivery_stages']={'write':{'status':'in_progress'}}
        env.store._save(db,state,'test_delivery_progress',{})
    invoke(env,review);assert calls==[1]


def test_validate_keeps_scoped_gateway_failure_blocking(env,monkeypatch):
    from ai_system.graph.nodes import consistency,validate
    from ai_system.graph.context import GraphContext
    from ai_system.research.citations import audit_citations
    ctx=GraphContext.create(str(env.store.root),planner_llm=SimpleNamespace(model_name='offline-model'));ctx.research=env.store
    ctx.registry=env.ctx.registry
    async def failed(*args,**kwargs):raise TimeoutError()
    async def no_citations(*args,**kwargs):return []
    monkeypatch.setattr(consistency,'call_structured',failed)
    monkeypatch.setattr('ai_system.research.citations.audit_citations',no_citations)
    monkeypatch.setattr(validate,'check_sections',lambda *args:[])
    outcome=asyncio.run(validate.validate(env.state,SimpleNamespace(context=ctx)))
    assert outcome['issues'] and outcome['research_restart']
    assert any(i.get('review_scope',{}).get('stage')=='sections' for i in outcome['issues'])
    assert not env.store.delivery_ready(env.store.snapshot())


def snapshots(env):
    pre=SimpleNamespace(values=copy.deepcopy(env.state),next=('reconcile',),config={'id':'pre'},parent_config={'id':'older'},metadata={'step':1,'source':'loop'},created_at=datetime.fromtimestamp(90,timezone.utc).isoformat())
    result={'conflicts':[{'claims':['C1'],'issue':'差值口径','resolution':'统一方向','research_issue':None}]}
    post=SimpleNamespace(values={**copy.deepcopy(env.state),'consistency':result['conflicts'],'research_restart':False},next=('write',),config={'id':'post'},parent_config=pre.config,metadata={'step':2,'source':'loop'},created_at=datetime.fromtimestamp(110,timezone.utc).isoformat())
    trace=[{'node':'reconcile','finish':'stop','seconds':10.0,'input':100,'output':50}]
    requests=[{'research_id':env.store.research_id,'http_status':200,'sent_at':100.0,'sequence':36}]
    return pre,post,result,trace,requests


def test_legacy_import_requires_checkpoint_context_and_actual_completed_trace(env):
    pre,post,result,trace,requests=snapshots(env)
    proof=completed_reconcile_proof(env.store,env.ctx,pre,post,result,trace,requests)
    assert proof['result']==result and proof['provenance']['model_request_sequence']==36
    for corruption in ['empty','failed','changed','no_trace']:
        p,q,r,t,u=snapshots(env)
        if corruption=='empty':q.values['consistency']=[];r={'conflicts':[]}
        elif corruption=='failed':q.next=('observe',)
        elif corruption=='changed':p.values['analysis']['requirements'][0]['statement']='changed'
        else:t[0]['finish']='length'
        with pytest.raises(ValueError):completed_reconcile_proof(env.store,env.ctx,p,q,r,t,u)


def test_legacy_section_failure_is_bound_without_resolving_or_clearing_citations(env):
    issue=env.store.report_issues([IssueReport(task_id='Q1',category='service',message='章节审查未完成：预算耗尽')],'sections')[0]
    env.store.save_sections(env.state['sections'],[])
    pre,post,_,_,_=snapshots(env);pre.next=('validate',);post.next=('observe',);post.values['issues']=[issue]
    proof=section_failure_proof(env.store,env.ctx,pre,post,{'issues':[issue]})
    assert proof['issue_id']==issue['id'] and proof['stage']=='sections'
    assert env.store.snapshot()['issues'][0]['status']=='confirmed'
    pre.values['sections']['abstract']+=' changed'
    with pytest.raises(ValueError):section_failure_proof(env.store,env.ctx,pre,post,{'issues':[issue]})


def test_authorized_extension_preserves_count_and_is_idempotent(tmp_path):
    from ai_system.research.runtime import ResearchRuntime
    runtime=ResearchRuntime(tmp_path,'same-study');runtime.configure({'request_limit':2,'timeout_seconds':90,'compact_paper':True,'confirm_models':True})
    runtime.reserve('example.test');runtime.reserve('example.test')
    receipt=runtime.extend_limit(2,expected_limit=2,command_id='approved',authorization={'source':'offline-test'})
    assert receipt['new_limit']==4 and runtime.status()['used']==2 and runtime.policy()['timeout_seconds']==90
    runtime.extend_limit(2,expected_limit=2,command_id='approved',authorization={'source':'offline-test'})
    assert runtime.status()['request_limit']==4 and runtime.status()['used']==2
    with pytest.raises(ValueError):runtime.extend_limit(5,expected_limit=2,command_id='different',authorization='offline-test')
    with pytest.raises(ValueError):runtime.extend_limit(6,expected_limit=4,command_id='too-many',authorization='offline-test')


def test_resume_keeps_service_failure_open_until_matching_review_completes(env):
    async def failure():raise TimeoutError()
    with pytest.raises(TimeoutError):invoke(env,failure,'sections',env.state['sections'])
    original=env.store.snapshot()['issues'][0]
    env.store.recover()
    assert env.store.snapshot()['issues'][0]['status']=='confirmed'
    async def success():return ClaimAudit(issues=[])
    invoke(env,success,'sections',env.state['sections'])
    issue=env.store.snapshot()['issues'][0]
    assert issue['id']==original['id'] and issue['status']=='resolved' and issue['review_resolution']


@pytest.mark.parametrize('stage',['reconcile','sections'])
def test_unknown_review_identifiers_are_failed_not_silently_dropped(env,monkeypatch,stage):
    from ai_system.graph.context import GraphContext
    from ai_system.graph.nodes import consistency
    from ai_system.graph.state import ClaimIssue
    ctx=GraphContext.create(str(env.store.root),planner_llm=None);ctx.research=env.store;ctx.registry=env.ctx.registry
    async def malformed(*args,validate=None,**kwargs):
        answer=(ConsistencyReview(conflicts=[Conflict(claims=['C999'],issue='Unknown',resolution='Unknown')])
                if stage=='reconcile' else ClaimAudit(issues=[ClaimIssue(section='unknown',problem='Unknown')]))
        validate(answer)
        return answer
    monkeypatch.setattr(consistency,'call_structured',malformed)
    if stage=='reconcile':
        result=asyncio.run(consistency.reconcile(env.state,SimpleNamespace(context=ctx)))
        assert result['research_restart'] is True
    else:asyncio.run(consistency.audit_sections(env.state,ctx))
    issues=env.store.snapshot()['issues']
    assert any(i['category']=='service' and i['status']=='confirmed' for i in issues)
    with env.store._connect() as db:
        assert not db.execute("SELECT 1 FROM review_records WHERE state='completed'").fetchone()
        assert db.execute("SELECT 1 FROM review_records WHERE state='failed'").fetchone()


def test_shared_review_reply_still_runs_each_callers_contract_validation(env):
    async def scenario():
        started=asyncio.Event();release=asyncio.Event();calls=[]
        async def perform():started.set();await release.wait();calls.append(1);return ConsistencyReview(conflicts=[])
        first=asyncio.create_task(run_review(env.ctx,env.state,'reconcile',ConsistencyReview,'same','same',perform));await started.wait()
        def stricter(result):raise ValueError('caller_contract_rejected')
        second=asyncio.create_task(run_review(env.ctx,env.state,'reconcile',ConsistencyReview,'same','same',perform,validate=stricter))
        await asyncio.sleep(.01);release.set();assert (await first).conflicts==[]
        with pytest.raises(ValueError,match='caller_contract_rejected'):await second
        assert calls==[1]
    asyncio.run(scenario())
