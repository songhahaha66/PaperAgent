"""Current conclusion checks and read-only historical segmented proof validation."""
import asyncio,copy
from types import SimpleNamespace
import pytest
from helpers.review import env,no_network
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
