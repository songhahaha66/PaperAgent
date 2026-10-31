"""Offline review-cache tests. Any real HTTP send fails this module immediately."""
from services.file_services.workspace_layout import workspace_path

import asyncio, json, sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.research.review_cache import ReviewCache,run_review,review_context
from ai_system.graph.state import ConsistencyReview,Conflict,ClaimAudit
from helpers.review import env, no_network


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
    elif change=='source_file':(workspace_path(env.store.root, 'runs/one/stdout.log')).write_text('changed execution output')
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
