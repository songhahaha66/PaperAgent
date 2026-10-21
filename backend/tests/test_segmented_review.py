"""Current conclusion checks and read-only historical segmented proof validation."""
import asyncio,copy
from types import SimpleNamespace
import pytest
from test_review_cache import env,no_network
from ai_system.modules.writing import consistency
from ai_system.graph.state import ClaimAudit,ClaimIssue
from ai_system.research import segmented_review as legacy
from ai_system.research.review_cache import ReviewCache,review_context
from ai_system.research.store import digest
from services.chat_services import review_tasks,issue_edits

TAIL='尾部错误主张：本研究已经证明所有未来数据均有效。'

@pytest.fixture
def long(env,monkeypatch):
    env.state['sections']={'evaluation':('研究条件和局限仍须审慎核对。\n\n'*700)+TAIL,
                           'abstract':'当前结果只适用于已有数据。'}
    env.store.save_sections(env.state['sections'],[])
    model=SimpleNamespace(model_name='offline-claims',calls=[],failure=False,gate=None)
    env.ctx.planner_llm=model;env.model=model
    async def controlled(llm,schema,messages,validate=None,**kwargs):
        assert schema is ClaimAudit
        prompt=messages[-1].content;model.calls.append(prompt)
        assert env.state['sections']['evaluation'] in prompt
        if model.gate:await model.gate.wait()
        if model.failure:raise TimeoutError('controlled review interruption')
        answer=schema(issues=[ClaimIssue(section='evaluation',problem='未来数据泛化超出已有证据',quote=TAIL)])
        if validate:validate(answer)
        return answer
    monkeypatch.setattr(consistency,'call_structured',controlled)
    monkeypatch.setattr(review_tasks,'call_structured',controlled)
    return env


def run(v):return asyncio.run(consistency.audit_sections(v.state,v.ctx))
def update(v,text):
    v.state['sections']['evaluation']=text;v.store.save_sections(v.state['sections'],[])


def test_tail_is_supplied_without_clipping_and_one_review_is_reused(long):
    v=long;assert v.state['sections']['evaluation'].find(TAIL)>6000
    assert any(i.section=='evaluation' for i in run(v))
    assert len(v.model.calls)==1
    run(v);assert len(v.model.calls)==1
    record=next(r for r in review_tasks.records(v.store) if r['state']=='completed')
    detail=record['context']['section_coverage']
    assert detail['complete'] and not detail['full_document'] and detail['policy']=='key-claims-v1'
    assert detail['sections']['evaluation']['included_chars']==len(v.state['sections']['evaluation'])
    assert all(r['stage']=='sections' for r in review_tasks.records(v.store))


def test_oversized_atomic_text_has_no_length_gate(long):
    v=long;update(v,'无标点的超长句子'*1000+TAIL)
    assert run(v) and len(v.model.calls)==1


def test_changed_source_invalidates_review_and_keeps_evidence(long):
    v=long;run(v);before=v.store.snapshot()
    update(v,v.state['sections']['evaluation']+'新增适用范围。');run(v)
    assert len(v.model.calls)==2
    after=v.store.snapshot()
    assert all(before[k]==after[k] for k in ('experiments','executions','artifacts','claims','validations'))


def test_interruption_is_incomplete_then_explicit_resume_reuses_the_same_draft(long):
    v=long;v.model.failure=True;before=v.store.snapshot();run(v)
    record=review_tasks.records(v.store)[0]
    assert record['state']=='failed' and record['error_code']=='TimeoutError'
    task=next(t for t in review_tasks.status(v.store)['tasks'] if t['stage']=='sections')
    assert task['state']=='failed' and task['can_resume'] and 'segmented' not in task
    v.model.failure=False
    async def scenario():
        review_tasks.start(v.store,'sections',task['version'],'resume-current-review',v.model)
        if review_tasks.ACTIVE:await asyncio.wait_for(asyncio.gather(*list(review_tasks.ACTIVE)),3)
    asyncio.run(scenario())
    task=next(t for t in review_tasks.status(v.store)['tasks'] if t['stage']=='sections')
    assert task['state']=='completed' and task['outcome']=='findings' and len(v.model.calls)==2
    finding=next(i for i in review_tasks.status(v.store)['issues'] if i['category']=='writing')
    assert issue_edits.context(v.store,finding['id'])['quote']==TAIL
    saved=next(i for i in v.store.snapshot()['feedback'] if i['id']==finding['id'])
    assert saved['review_scope']['generated_by_review']==task['record_id']
    assert review_tasks.status(v.store)['tasks']==review_tasks.status(v.store)['tasks'] and len(v.model.calls)==2
    after=v.store.snapshot()
    assert all(before[k]==after[k] for k in ('experiments','executions','artifacts','claims','validations','sections'))


@pytest.mark.parametrize('change',['source','evidence','independent_doubt','producer_receipt'])
def test_completed_findings_stay_current_until_an_independent_input_or_proof_changes(long,change):
    from ai_system.research.models import IssueReport
    v=long
    async def scenario():
        task=next(t for t in review_tasks.status(v.store)['tasks'] if t['stage']=='sections')
        review_tasks.start(v.store,'sections',task['version'],'first-complete-review',v.model)
        if review_tasks.ACTIVE:await asyncio.wait_for(asyncio.gather(*list(review_tasks.ACTIVE)),3)
    asyncio.run(scenario())
    completed=next(t for t in review_tasks.status(v.store)['tasks'] if t['stage']=='sections')
    assert completed['state']=='completed' and completed['outcome']=='findings' and len(v.model.calls)==1
    before=review_tasks.material(v.store,'sections')
    if change=='source':update(v,v.state['sections']['evaluation']+'新的适用条件。')
    elif change=='evidence':(v.store.root/'runs/one/stdout.log').write_text('changed actual execution output')
    elif change=='independent_doubt':
        v.store.report_issues([IssueReport(task_id='Q1',category='writing',section='evaluation',message='独立新增的样本范围疑点')],'sections')
    else:
        with v.store._transaction() as db:db.execute("UPDATE review_records SET state='failed' WHERE id=?",(completed['record_id'],))
    task=next(t for t in review_tasks.status(v.store)['tasks'] if t['stage']=='sections')
    assert task['state']!='completed' and task['version']!=completed['version']
    assert review_tasks.material(v.store,'sections')!=before
    assert len(v.model.calls)==1, 'status reads must neither recheck their own findings nor call the model'


def test_concurrent_same_review_has_one_owner(long):
    v=long
    async def scenario():
        v.model.gate=asyncio.Event()
        a=asyncio.create_task(consistency.audit_sections(v.state,v.ctx))
        await asyncio.sleep(.02)
        b=asyncio.create_task(consistency.audit_sections(v.state,v.ctx))
        await asyncio.sleep(.02);assert len(v.model.calls)==1
        v.model.gate.set();await asyncio.gather(a,b)
    asyncio.run(scenario());assert len(v.model.calls)==1


def test_late_old_version_response_cannot_complete_current_review(long):
    v=long
    async def scenario():
        v.model.gate=asyncio.Event()
        old=copy.deepcopy(v.state)
        work=asyncio.create_task(consistency.audit_sections(old,v.ctx))
        await asyncio.sleep(.02);update(v,v.state['sections']['evaluation']+'当前材料改变。')
        v.model.gate.set();await work
    asyncio.run(scenario())
    assert not any(r['state']=='completed' for r in review_tasks.records(v.store))


def test_cache_owner_renews_lease_without_new_scientific_work(long):
    v=long;cache=ReviewCache(v.store);record=cache.start('sections',{'version':'heartbeat'},source='recovery-intent')
    original=record['lease']['expires_at']
    async def scenario():
        heartbeat=asyncio.create_task(cache.heartbeat(record,interval=.01))
        await asyncio.sleep(.03);heartbeat.cancel();await asyncio.gather(heartbeat,return_exceptions=True)
    asyncio.run(scenario());assert record['lease']['expires_at']>original
    assert next(r for r in review_tasks.records(v.store) if r['id']==record['id'])['lease']==record['lease']


def historical_receipt(v):
    """Seed frozen old receipts directly; the retired dispatcher is not invoked."""
    sections={'evaluation':'已有数据范围内的结论。','abstract':'仅当前数据有效。'}
    state={**v.state,'sections':sections};metadata={};blocks=[]
    for key,text in sections.items():
        metadata[key]={'hash':digest(text),'length':len(text),'revision':1}
        binding={'section':key,'section_hash':digest(text),'revision':1,'start':0,'end':len(text),
                 'window_start':0,'window_end':len(text),'core_hash':digest(text),'window_hash':digest(text)}
        blocks.append({**binding,'id':digest(binding),'text':text,'left':''})
    plan={'policy':legacy.POLICY,'atom_limit':legacy.ATOM_LIMIT,'offset_unit':'unicode_code_point','sections':metadata,'blocks':blocks}
    plan['id']=digest(plan);cache=ReviewCache(v.store);bindings=[]
    for block in blocks:
        context,_,_=legacy.block_material(v.ctx,state,block)
        answer=legacy.BlockAudit(issues=[],statements=[legacy.Statement(quote=block['text'])])
        row=cache.start('section_blocks',context);row=cache.complete(row,answer.model_dump())
        bindings.append((block,answer,row))
    context,_,_,ledger=legacy.global_material(v.ctx,state,bindings)
    answer=legacy.GlobalAudit(block_keys=[r['key'] for r in ledger],issues=[])
    global_row=cache.complete(cache.start('section_global',context),answer.model_dump())
    context=review_context(v.ctx,state,'sections',legacy.POLICY,plan['id'],ClaimAudit,sections=sections,
        extra={'non_blocking':False,'segmented_plan':plan,'prompt_contract':{
            'requirements':state['analysis']['requirements'],'assumptions':state['analysis']['assumptions'],'consistency':state.get('consistency') or []}})
    context['section_coverage']={'policy':legacy.POLICY,'complete':True,'sections':{
        k:{'total_chars':m['length'],'included_chars':m['length']} for k,m in metadata.items()},'plan_id':plan['id']}
    proof={'blocks':[{'binding_id':b['id'],'review_id':r['id'],'result_hash':r['result_hash'],'reuse_key':r['context']['extra']['reuse_key']} for b,_,r in bindings],
           'global':{'review_id':global_row['id'],'result_hash':global_row['result_hash']},'policy':legacy.POLICY}
    return cache.complete(cache.start('sections',context,'segmented-aggregate'),{'issues':[]},provenance=proof)


def test_existing_segmented_receipts_remain_readable_and_verifiable(env):
    record=historical_receipt(env)
    assert legacy.verify_proof(env.store,record)
    assert ReviewCache(env.store).get('sections',record['context'],ClaimAudit)
    assert any(r['id']==record['id'] for r in review_tasks.records(env.store))
    assert not hasattr(legacy,'audit')


@pytest.mark.parametrize('damage',['missing','duplicate','hash','version','global','result'])
def test_incomplete_or_corrupt_historical_proofs_are_not_success(env,damage):
    record=copy.deepcopy(historical_receipt(env));proof=record['provenance'];plan=record['context']['extra']['segmented_plan']
    if damage=='missing':proof['blocks'].pop()
    elif damage=='duplicate':proof['blocks'][-1]=proof['blocks'][0]
    elif damage=='hash':plan['blocks'][0]['text']+='altered'
    elif damage=='version':plan['sections']['evaluation']['revision']+=1
    elif damage=='global':proof['global']['review_id']='missing'
    else:record['result']={'issues':[{'section':'abstract','problem':'invented'}]}
    assert not legacy.verify_proof(env.store,record)


def test_deleted_historical_child_invalidates_receipt(env):
    record=historical_receipt(env);rid=record['provenance']['blocks'][0]['review_id']
    with env.store._transaction() as db:db.execute('DELETE FROM review_records WHERE id=?',(rid,))
    assert not legacy.verify_proof(env.store,record)
    assert not ReviewCache(env.store).get('sections',record['context'],ClaimAudit)
