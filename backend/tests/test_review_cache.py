"""Offline review-cache tests. Any real HTTP send fails this module immediately."""

import asyncio, json, sys
from pathlib import Path
from types import SimpleNamespace
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.research.store import ResearchStore
from ai_system.research.models import WorkerResult, ReviewRecord
from ai_system.research.validation import report_fingerprint
from ai_system.research.review_cache import ReviewCache,run_review,review_context
from ai_system.research.review_migration import context_from_store
from ai_system.graph.state import ConsistencyReview,Conflict,ClaimAudit
from ai_system.research.control import graph_view
from research_tools_helpers import answer


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
    report={'status':'completed','summary':'实测score为 {{R:R1}}，基线为 {{R:R2}}。',
        'key_findings':[{'statement':c['statement'],'strength':c['strength']} for c in claims],
        'requirement_evidence':[{'requirement_id':'RQ1','evidence_refs':['R1']}]}
    store.commit(WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',results=results,claims=claims,report=report,
        reviews=[ReviewRecord(report_hash=report_fingerprint(report),issues=[])]))
    before=store.snapshot()
    answer(store,question_id=before['experiments']['Q1']['question_id'],
        statement='本次输入下score为 {{R:R1}}，满足有限数检查。',artifact_refs=['R1'],
        coverage='answered',stop_reason='冻结的有限数检查已通过。',limitations=['该检查不证明模型效果或外推有效性。'])
    snapshot=store.snapshot()
    assert snapshot['executions']==before['executions'] and snapshot['artifacts']==before['artifacts']
    assert store.delivery_ready(snapshot)
    state=graph_view(snapshot)
    state.update(sections={'abstract':'摘要正文。'*30,'evaluation':'证据局限。'*30},consistency=[])
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
