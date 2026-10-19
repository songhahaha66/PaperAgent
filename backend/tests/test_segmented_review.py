"""Offline deterministic oracles; any real HTTP is forbidden by the shared fixture."""
import asyncio,copy,json,time
from types import SimpleNamespace
import pytest
from test_review_cache import env,no_network
from ai_system.research import segmented_review as seg
from ai_system.research.review_cache import ReviewCache
from ai_system.research.runtime import ResearchRuntime,ACTIVE_BUDGET,budget_scope
from ai_system.research.store import digest
from ai_system.graph.state import ClaimIssue
from services.chat_services import review_tasks,issue_edits

TAIL='尾部错误主张：本研究已经证明所有未来数据均有效。'
BOUNDARY_LEFT='仅在当前划分上观察到改善。'
BOUNDARY_RIGHT='但是这里把上一段的改善称为已证明的统计显著性。'

@pytest.fixture
def long(env,monkeypatch):
    paragraphs=[f'段落{chr(65+i)}。'+('研究条件和局限仍须审慎核对。'*90) for i in range(5)]
    env.state['sections']={'evaluation':'\n\n'.join(paragraphs)+'\n\n'+TAIL,'abstract':'当前结果只适用于已有数据。'}
    env.store.save_sections(env.state['sections'],[])
    model=SimpleNamespace(model_name='offline-segmented',calls=[],fail=None,gate=None,reserve=False,global_bad=False)
    env.ctx.planner_llm=model;env.model=model
    async def controlled(llm,schema,messages,validate=None,**kwargs):
        if schema is seg.BlockAudit:
            payload=json.loads(messages[-1].content.split('# 范围说明\n')[1]);core=payload['core'];left=payload['left_context'];tag=digest(core)
            model.calls.append(('block',tag))
            if model.reserve:ACTIVE_BUDGET.get().reserve('offline.invalid')
            if model.gate:await model.gate.wait()
            if model.fail==tag:raise TimeoutError('controlled block interruption')
            findings=[]
            if TAIL in core:findings.append(ClaimIssue(section=payload['section'],problem='未来数据泛化超出已有证据',quote=TAIL))
            if BOUNDARY_LEFT in left and BOUNDARY_RIGHT in core:findings.append(ClaimIssue(section=payload['section'],problem='相邻段把实测改善误称为显著',quote=BOUNDARY_RIGHT))
            statements=[seg.Statement(quote=q) for q in ('结论甲：误差方向为正。','结论乙：同一误差方向为负。') if q in core]
            answer=schema(issues=findings,statements=statements)
        else:
            assert schema is seg.GlobalAudit
            model.calls.append(('global',None))
            if model.reserve:ACTIVE_BUDGET.get().reserve('offline.invalid')
            ledger=json.loads(messages[-1].content[messages[-1].content.rfind('\n[')+1:]);keys=[r['key'] for r in ledger]
            quotes=[s['quote'] for r in ledger for s in r['statements']]
            findings=[ClaimIssue(section='evaluation',problem='跨节同一误差方向相反',quote='结论甲：误差方向为正。')] if len(quotes)>=2 else []
            answer=schema(block_keys=keys[:-1] if model.global_bad else keys,issues=findings)
        if validate:validate(answer)
        return answer
    monkeypatch.setattr(seg,'call_structured',controlled)
    return env


def run(v):return asyncio.run(seg.audit(v.ctx,v.state))
def update(v,texts):v.state['sections']=texts;v.store.save_sections(texts,[])
def row(v,source='segmented-aggregate'):return next(r for r in review_tasks.records(v.store) if r['source']==source and r['state']=='completed')


def test_tail_after_6000_is_found_and_every_character_has_verified_coverage(long):
    v=long;assert v.state['sections']['evaluation'].find(TAIL)>6000
    answer=run(v);assert any(i.quote==TAIL for i in answer.issues)
    record=row(v);assert seg.verify_proof(v.store,record)
    progress=seg.progress(v.ctx,v.state);assert progress['covered_chars']==progress['total_chars'] and progress['completed_blocks']==progress['total_blocks']
    assert progress['global']['state']=='completed' and v.model.calls[-1][0]=='global'
    before=len(v.model.calls);run(v);assert len(v.model.calls)==before


def test_sentence_formula_reference_boundaries_and_adjacent_context_are_exact(long):
    text='第一段。\n\n'+r'公式 $a+b$ 与引用 {{R:R1}} 和 {{C:C1}}。'+'\n\n'+r'\begin{align}x &= y\\z &= t\end{align}'+'\n\n末段。'
    plan=seg.plan_for({'chapter':text},{'chapter':{'revision':3}});assert seg.validate_plan(plan)
    assert ''.join(b['text'] for b in plan['blocks'])==text
    for b in plan['blocks']:
        assert b['revision']==3 and b['section_hash']==digest(text)
        assert not any(a<b['start']<z or a<b['end']<z for a,z in seg.protected(text))
    v=long;texts=dict(v.state['sections']);texts['evaluation']=texts['evaluation'].replace(TAIL,BOUNDARY_LEFT+'\n\n'+BOUNDARY_RIGHT);update(v,texts)
    assert any(i.quote==BOUNDARY_RIGHT for i in run(v).issues)


def test_cross_section_check_is_independent_of_passing_local_blocks(long):
    v=long;texts=dict(v.state['sections']);texts['evaluation']=texts['evaluation'].replace(TAIL,'结论甲：误差方向为正。');texts['abstract']='结论乙：同一误差方向为负。';update(v,texts)
    result=run(v);record=row(v)
    assert all(not r['result']['issues'] for r in review_tasks.records(v.store) if r['stage']=='section_blocks' and r['state']=='completed')
    assert any('跨节' in i.problem for i in result.issues) and record['provenance']['global']


def test_middle_edit_invalidates_only_own_and_next_window_and_global(long):
    v=long;run(v);before=len(v.model.calls);parts=v.state['sections']['evaluation'].split('\n\n');parts[2]+='补充范围说明。'
    texts=dict(v.state['sections']);texts['evaluation']='\n\n'.join(parts);update(v,texts)
    progress=seg.progress(v.ctx,v.state);assert progress['total_blocks']-progress['completed_blocks']==2 and progress['global']['state']=='pending'
    run(v);calls=v.model.calls[before:];assert [r[0] for r in calls].count('block')==2 and calls[-1][0]=='global'
    assert seg.verify_proof(v.store,row(v))


@pytest.mark.parametrize('damage',['missing','duplicate','hash','version','global','result'])
def test_incomplete_or_corrupt_compositions_never_count_as_completed(long,damage):
    v=long;run(v);record=copy.deepcopy(row(v));proof=record['provenance'];plan=record['context']['extra']['segmented_plan']
    if damage=='missing':proof['blocks'].pop()
    elif damage=='duplicate':proof['blocks'][-1]=proof['blocks'][0]
    elif damage=='hash':plan['blocks'][0]['text']+='altered'
    elif damage=='version':plan['sections']['evaluation']['revision']+=1
    elif damage=='global':proof['global']['review_id']='missing'
    else:record['result']={'issues':[]}
    assert not seg.verify_proof(v.store,record)


def test_partial_failure_keeps_completed_blocks_and_explicit_resume_reuses_them(long):
    v=long;plan=seg.make_plan(v.ctx,v.state);v.model.fail=plan['blocks'][2]['core_hash']
    with pytest.raises((TimeoutError,seg.CoverageError)):run(v)
    first=seg.progress(v.ctx,v.state);assert first['completed_blocks']==2 and first['global']['state']=='pending'
    prior=list(v.model.calls);v.model.fail=None;run(v)
    assert all(v.model.calls.count(call)==1 for call in prior[:2]);assert seg.progress(v.ctx,v.state)['global']['state']=='completed'


def test_budget_exhaustion_preserves_usage_and_completed_block(long):
    v=long;runtime=ResearchRuntime(v.store.root);runtime.configure({'request_limit':1,'timeout_seconds':90});v.model.reserve=True
    async def scenario():
        with budget_scope(v.store.root,v.store.research_id):
            with pytest.raises(ResearchBudgetExhausted):await seg.audit(v.ctx,v.state)
    from ai_system.research.runtime import ResearchBudgetExhausted
    asyncio.run(scenario());assert runtime.status()['used']==1 and seg.progress(v.ctx,v.state)['completed_blocks']==1
    with pytest.raises(ResearchBudgetExhausted):run(v)
    assert runtime.status()['used']==1


def test_concurrent_resume_has_one_owner_and_late_old_version_cannot_complete(long):
    v=long
    async def scenario():
        v.model.gate=asyncio.Event();a=asyncio.create_task(seg.audit(v.ctx,v.state));await asyncio.sleep(.02)
        b=asyncio.create_task(seg.audit(v.ctx,v.state));await asyncio.sleep(.02);assert len(v.model.calls)==1
        v.model.gate.set();await asyncio.gather(a,b)
        assert len(v.model.calls)==len(seg.make_plan(v.ctx,v.state)['blocks'])+1
    asyncio.run(scenario())


def test_wrong_global_ack_never_promotes_local_coverage_to_global_success(long):
    v=long;v.model.global_bad=True
    with pytest.raises((TimeoutError,seg.CoverageError)):run(v)
    p=seg.progress(v.ctx,v.state);assert p['completed_blocks']==p['total_blocks'] and p['global']['state']!='completed'
    assert not any(r['state']=='completed' and r['source']=='segmented-aggregate' for r in review_tasks.records(v.store))


def test_oversized_atomic_content_is_explicitly_blocked_without_a_model_call(long):
    v=long;update(v,{'evaluation':'无标点的超长句子'*1000})
    with pytest.raises(seg.CoverageError):run(v)
    assert not v.model.calls


def test_current_queue_failure_resume_reports_progress_and_tail_location(long):
    v=long;plan=seg.make_plan(v.ctx,v.state);v.model.fail=plan['blocks'][2]['core_hash']
    def task():return next(t for t in review_tasks.status(v.store)['tasks'] if t['stage']=='sections')
    async def drain():
        if review_tasks.ACTIVE:await asyncio.wait_for(asyncio.gather(*list(review_tasks.ACTIVE)),3)
    async def scenario():
        t=task();assert t['can_resume'] and t['segmented']['completed_blocks']==0
        first=review_tasks.start(v.store,'sections',t['version'],'segmented-first',v.model)
        duplicate=review_tasks.start(v.store,'sections',t['version'],'segmented-duplicate',v.model);assert duplicate['id']==first['id']
        await drain();t=task();assert t['state']=='failed' and t['segmented']['completed_blocks']==2
        v.model.fail=None;review_tasks.start(v.store,'sections',t['version'],'segmented-resume',v.model);await drain()
        t=task();assert t['state']=='completed' and t['outcome']=='findings' and t['segmented']['global']['state']=='completed'
        finding=next(i for i in v.store.snapshot()['issues'] if i['category']=='writing')
        located=issue_edits.context(v.store,finding['id']);assert located['quote']==TAIL and located['origin_current']
        assert finding['paper_origin']['review_id']
    asyncio.run(scenario())


def test_late_response_keeps_old_aggregate_superseded_without_overwriting_current(long):
    v=long
    async def scenario():
        v.model.gate=asyncio.Event();work=asyncio.create_task(seg.audit(v.ctx,copy.deepcopy(v.state)));await asyncio.sleep(.02)
        texts=dict(v.state['sections']);texts['abstract']+='条件已经改变。';update(v,texts);v.model.gate.set()
        with pytest.raises((ReviewSuperseded,seg.CoverageError,ValueError)):await work
        assert not any(r['source']=='segmented-aggregate' and r['state']=='completed' for r in review_tasks.records(v.store))
    from ai_system.research.review_cache import ReviewSuperseded
    asyncio.run(scenario())


def test_deleted_child_invalidates_cached_aggregate_and_current_success(long):
    v=long;run(v);record=row(v);rid=record['provenance']['blocks'][0]['review_id']
    with v.store._transaction() as db:db.execute('DELETE FROM review_records WHERE id=?',(rid,))
    assert not seg.verify_proof(v.store,record)
    task=next(t for t in review_tasks.status(v.store)['tasks'] if t['stage']=='sections');assert task['state']!='completed'
    before=len(v.model.calls);run(v);assert len(v.model.calls)-before==1  # global ledger remains identical after exact recomputation


def test_empty_body_and_references_are_in_manifest_and_valid_math_is_atomic():
    text=r'\[x+y=z\]'+'\n\n'+r'\(x=y\)'+'\n\n$$x=z$$\n\n'+r'见\cite{source}与{{C:C1}}。'
    plan=seg.plan_for({'chapter':text,'references':'文献原文。','empty':''});assert seg.validate_plan(plan)
    assert sum(b['end']-b['start'] for b in plan['blocks'])==len(text)+len('文献原文。')


def test_long_running_owner_renews_existing_lease(long):
    v=long;cache=ReviewCache(v.store);record=cache.start('sections',{'version':'heartbeat'},source='recovery-intent')
    original=record['lease']['expires_at']
    async def scenario():
        heartbeat=asyncio.create_task(cache.heartbeat(record,interval=.01));await asyncio.sleep(.03);heartbeat.cancel();await asyncio.gather(heartbeat,return_exceptions=True)
    asyncio.run(scenario());assert record['lease']['expires_at']>original
    saved=next(r for r in review_tasks.records(v.store) if r['id']==record['id']);assert saved['lease']==record['lease']


def test_explicit_offline_budget_extension_resumes_without_reset_or_repeating_blocks(long):
    v=long;runtime=ResearchRuntime(v.store.root);runtime.configure({'request_limit':1,'timeout_seconds':90});v.model.reserve=True
    from ai_system.research.runtime import ResearchBudgetExhausted
    with pytest.raises(ResearchBudgetExhausted):run(v)
    original=v.model.calls[0];assert runtime.status()['used']==1
    runtime.extend_limit(5,expected_limit=1,command_id='fixture-only-approval-1',authorization={'scope':'offline-fixture'})
    with pytest.raises(ResearchBudgetExhausted):run(v)
    assert runtime.status()['used']==6
    runtime.extend_limit(2,expected_limit=6,command_id='fixture-only-approval-2',authorization={'scope':'offline-fixture'})
    run(v);assert runtime.status()['used']==8 and v.model.calls.count(original)==1


def test_input_context_change_invalidates_all_dependent_blocks(long):
    v=long;run(v);(v.store.root/'data/input.csv').write_text('x\n99\n')
    progress=seg.progress(v.ctx,v.state);assert progress['completed_blocks']==0 and progress['global']['state']=='pending'


def test_aggregate_receipt_can_be_rebuilt_from_verified_cache_with_zero_budget(long):
    v=long;run(v);calls=len(v.model.calls)
    with v.store._transaction() as db:db.execute("DELETE FROM review_records WHERE stage='sections'")
    runtime=ResearchRuntime(v.store.root);runtime.configure({'request_limit':0,'timeout_seconds':90})
    task=next(t for t in review_tasks.status(v.store)['tasks'] if t['stage']=='sections')
    assert task['can_resume'] and task['segmented']['remaining_requests_upper_bound']==0
    run(v);assert len(v.model.calls)==calls and seg.verify_proof(v.store,row(v)) and runtime.status()['used']==0


def test_global_ledger_limit_keeps_local_success_but_refuses_full_completion(long,monkeypatch):
    v=long;monkeypatch.setattr(seg,'GLOBAL_LIMIT',100)
    with pytest.raises(seg.CoverageError):run(v)
    progress=seg.progress(v.ctx,v.state);assert progress['completed_blocks']==progress['total_blocks'] and progress['global']['state']=='blocked'
    assert not any(r['source']=='segmented-aggregate' and r['state']=='completed' for r in review_tasks.records(v.store))


def test_proposed_body_binding_names_next_revision_without_changing_source(long):
    v=long;source=v.store.snapshot()['sections']['evaluation'];state=copy.deepcopy(v.state);state['sections']['evaluation']+='新增适用范围。'
    plan=seg.make_plan(v.ctx,state)
    assert plan['sections']['evaluation']['revision']==source['revision']+1
    assert v.store.snapshot()['sections']['evaluation']==source
