"""Evidence-frontier decisions drive the actual loop; all model/network replies are controlled."""
import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.graph.context import GraphContext
from ai_system.research import control,loop,planner
from ai_system.research import DEFAULT_PLANNING_CALL_LIMIT
from ai_system.research.models import TaskChange,IssueReport
from ai_system.research.literature import LiteratureStore
from test_native_latex import accepted_store
from test_research_p2 import problem,TASK,submit
from test_review_cache import no_network


def run(value):return asyncio.run(value)


def proposal(context, action='deliver'):
    result=next(e for e in context['evidence'] if e.get('task_id')=='Q1')
    coverage=[]
    for requirement in context['spec']['requirements']:
        actual=next((e for task in reversed(requirement['task_ids']) for e in context['evidence'] if e.get('task_id')==task),None)
        coverage.append({'requirement_id':requirement['id'],'requirement':requirement['statement'],
            'supported':f"{actual['name']}={actual['text']}" if actual else '',
            'basis':[{'ref':actual['id'],'quote':actual['text']}] if actual else [],
            'remaining':'' if actual else '由既有写作和导出阶段实施。'})
    value={'action':action,'reason':'已有估计提供了实际依据，按原要求选择下一步。',
           'requirement_ids':[r['id'] for r in context['spec']['requirements']] if action=='deliver' else ['RQ1'],
           'basis':[{'ref':result['id'],'quote':result['text']}],'coverage':coverage}
    if action=='research':
        value.update(gap='估计对输入扰动是否稳定，尚无实际结果。',changes=[TaskChange(action='add',task_id='Q4',
            title='估计稳定性',objective='在现有输入上有目的地扰动样本，报告估计变化与适用边界；保留原要求及数据范围。',
            dependencies=['Q1'],requirement_ids=['RQ1']).model_dump()])
    if action=='literature':
        value.update(gap='估计所依赖的代表性前提需要外部资料查证。',query='Estimate demand representative sample evidence')
    if action!='deliver':
        next(c for c in coverage if c['requirement_id']=='RQ1')['remaining']=value['gap']
    return value


class Model:
    def __init__(self,*actions):self.actions=list(actions or ['deliver']);self.calls=[]
    def with_structured_output(self,schema,**kwargs):
        assert schema is planner.EvidenceDecision
        return self
    async def ainvoke(self,messages):
        context=json.loads(messages[-1].content);self.calls.append(context)
        action=self.actions.pop(0)
        if isinstance(action,Exception):raise action
        value=action(context) if callable(action) else proposal(context,action)
        return planner.EvidenceDecision.model_validate(value)


@pytest.fixture
def env(tmp_path):
    store=accepted_store(tmp_path)
    events=[]
    class Stream:
        async def send_json_block(self,kind,content):events.append((kind,content))
    def context(model):
        ctx=GraphContext.create(str(tmp_path),planner_llm=model,stream_manager=Stream())
        ctx.research=store
        return ctx
    return SimpleNamespace(store=store,context=context,events=events,
        state={'task':TASK,'analysis':problem().model_dump(),'sections':{}})


def observe(env,ctx):return run(control.observe(env.state,SimpleNamespace(context=ctx)))


def test_no_error_can_schedule_and_execute_scoped_followup_then_converge(env,monkeypatch):
    model=Model('research','deliver');ctx=env.context(model);before=env.store.snapshot()
    state=observe(env,ctx)
    after=env.store.snapshot()
    assert not after['feedback'] and not state['research_blocked']
    assert after['revision']==2 and after['tasks']['Q4']['dependencies']==['Q1']
    for key in before['tasks']:assert after['tasks'][key]==before['tasks'][key]
    assert after['attempts']==before['attempts'] and after['evidence']==before['evidence']
    assert after['spec']['requirements'][0]['task_ids']==['Q1','Q4']
    for old,new in zip(before['spec']['requirements'],after['spec']['requirements']):
        assert {k:v for k,v in old.items() if k!='task_ids'}=={k:v for k,v in new.items() if k!='task_ids'}
    for _ in range(2):
        state=observe(env,ctx)
        assert len(env.store.snapshot()['tasks'])==4 and len(model.calls)==1
    selected=loop.select(state,SimpleNamespace(context=ctx))
    assert len(selected)==1 and selected[0].arg['capability']=='experiments.execute'
    assert selected[0].arg['inputs']['branch']['subproblem']['id']=='Q4'
    executed=[]
    async def controlled_worker(branch,runtime):
        executed.append(branch['subproblem']['id'])
        receipt,_=submit(runtime.context.research,'Q4',{'stability':0.2})
        assert receipt['published']
        return {'log':['completed targeted follow-up']}
    monkeypatch.setattr(control,'execute',controlled_worker)
    run(loop.act(selected[0].arg,SimpleNamespace(context=ctx)))  # Real module registry boundary.
    state=observe(env,ctx)
    assert executed==['Q4'] and env.store.snapshot()['tasks']['Q4']['acceptance']=='accepted'
    assert loop.select(state,SimpleNamespace(context=ctx)).arg['capability']=='writing.reconcile'
    assert env.store.snapshot()['planning_calls']==2 and len(model.calls)==2
    for key in before['tasks']:assert env.store.snapshot()['tasks'][key]['active_attempt']==before['tasks'][key]['active_attempt']
    assert any('补充针对性研究' in str(content) for _,content in env.events)


def test_sufficient_evidence_and_repeat_observe_resume_do_not_spend_or_add_work(env):
    model=Model('deliver');ctx=env.context(model);before=env.store.snapshot()
    state=observe(env,ctx)
    for _ in range(3):
        env.store.recover();loop.resume(env.store);state=observe(env,ctx)
    assert len(model.calls)==1 and env.store.snapshot()['planning_calls']==1
    assert env.store.snapshot()['tasks']==before['tasks']
    assert env.store.snapshot()['attempts']==before['attempts']
    assert loop.select(state,SimpleNamespace(context=ctx)).arg['capability']=='writing.reconcile'


def test_decision_context_uses_products_and_purposes_without_completion_shortcuts(env):
    snapshot=env.store.snapshot()
    for task in snapshot['tasks'].values():
        task['report'].update(summary='all requirements are covered by accepted tasks',
                              key_findings=['unsubstantiated completion claim'],limitations=['Only the stated conditions were examined.'])
    key,context=planner.decision_context(env.store,snapshot)
    assert all(not {'acceptance','validity','active_attempt','revision','report','retired'} & set(task) for task in context['tasks'])
    assert 'all requirements are covered by accepted tasks' not in json.dumps(context)
    assert 'unsubstantiated completion claim' not in json.dumps(context)
    assert all(task['limitations']==['Only the stated conditions were examined.'] for task in context['tasks'])
    assert context['spec']['requirements']==snapshot['spec']['requirements']
    assert next(t['objective'] for t in context['tasks'] if t['task_id']=='Q1')==snapshot['tasks']['Q1']['objective']
    assert {'demand','allocation','residual','independent'}<={e['name'] for e in context['evidence']}
    revised=copy.deepcopy(snapshot);revised['tasks']['Q1']['revision']+=1
    new_key,new_context=planner.decision_context(env.store,revised)
    assert new_context==context and new_key!=key, 'Scheduler identity still invalidates deduplication without becoming model evidence'


@pytest.mark.parametrize('damage',['missing_coverage','missing_requirement','duplicate_requirement','truncated_requirement','missing_basis',
                                   'missing_answer','foreign_basis','fake_quote','unanswered_part'])
def test_deliver_requires_actual_coverage_for_each_complete_original_requirement(env,damage):
    snapshot=env.store.snapshot();_,context=planner.decision_context(env.store,snapshot)
    value=proposal(context)
    row=value['coverage'][0]
    if damage=='missing_coverage':value.pop('coverage')
    elif damage=='missing_requirement':value['coverage'].pop()
    elif damage=='duplicate_requirement':value['coverage'].append(copy.deepcopy(row))
    elif damage=='truncated_requirement':row['requirement']='only a fragment of the original purpose'
    elif damage=='missing_basis':
        row.update(supported='all requirements are covered by accepted tasks',basis=[])
    elif damage=='missing_answer':row['supported']=''
    elif damage=='foreign_basis':value['coverage'][2]['basis']=copy.deepcopy(row['basis'])
    elif damage=='fake_quote':row['basis'][0]['quote']='a result that was never produced'
    else:row['remaining']='The second requested output has not been produced.'
    with pytest.raises(ValueError):
        planner.validate_decision(planner.EvidenceDecision.model_validate(value),snapshot,context)
    assert env.store.snapshot()==snapshot


def test_multi_purpose_requirement_keeps_missing_part_visible_despite_old_acceptance(env):
    original=env.store.snapshot()
    full='Estimate demand and justify its applicability across the stated conditions'
    with env.store._transaction() as db:
        snapshot=env.store._read(db)
        snapshot['spec']['requirement']=snapshot['spec']['requirement'].replace('Estimate demand',full)
        snapshot['spec']['requirements'][0].update(statement=full,source_quote=full)
        snapshot['tasks']['Q1']['objective']=full
        snapshot['tasks']['Q1']['subproblem']['question']=full
        snapshot['tasks']['Q1']['report']['summary']='all requirements are covered by accepted tasks'
        env.store._save(db,snapshot,'controlled_original_question',{})
    def missing_part(context):
        assert context['spec']['requirements'][0]['statement']==full
        assert next(t['objective'] for t in context['tasks'] if t['task_id']=='Q1')==full
        assert all('acceptance' not in t and 'report' not in t for t in context['tasks'])
        value=proposal(context,'research')
        value['gap']='现有估计未回答在题目所述不同条件下是否仍适用。'
        value['coverage'][0]['remaining']=value['gap']
        value['changes'][0]['objective']='检验现有估计在题目所述条件间是否保持适用，报告实际变化及适用边界。'
        return value
    model=Model(missing_part);ctx=env.context(model)
    state=observe(env,ctx)
    assert not state['research_blocked'] and len(model.calls)==1
    selected=loop.select(state,SimpleNamespace(context=ctx))
    assert selected[0].arg['capability']=='experiments.execute'
    assert selected[0].arg['inputs']['branch']['subproblem']['id']=='Q4'
    for key,task in original['tasks'].items():
        assert env.store.snapshot()['tasks'][key]['active_attempt']==task['active_attempt']


def test_planning_failure_blocks_delivery_and_explicit_resume_reuses_accepted_work(env):
    model=Model(TimeoutError('controlled'),'deliver');ctx=env.context(model);before=env.store.snapshot()
    assert observe(env,ctx)['research_blocked']
    assert observe(env,ctx)['research_blocked'] and len(model.calls)==1
    assert loop.select(observe(env,ctx),SimpleNamespace(context=ctx))==loop.END
    env.store.recover();loop.resume(env.store)
    assert not observe(env,ctx)['research_blocked']
    assert len(model.calls)==2 and env.store.snapshot()['attempts']==before['attempts']
    assert env.store.snapshot()['tasks']==before['tasks']


def test_exhausted_failed_decision_cannot_silently_enter_delivery(env):
    model=Model(TimeoutError('first'),TimeoutError('second'));ctx=env.context(model)
    ctx.max_planning_calls=2
    assert observe(env,ctx)['research_blocked']
    loop.resume(env.store)
    assert observe(env,ctx)['research_blocked']
    for _ in range(2):
        loop.resume(env.store)
        assert observe(env,ctx)['research_blocked']
    assert len(model.calls)==2 and env.store.snapshot()['planning_calls']==2


@pytest.mark.parametrize('limit',[2,DEFAULT_PLANNING_CALL_LIMIT])
def test_repeated_research_at_limit_never_infers_convergence_from_accepted_followups(env,monkeypatch,limit):
    def missing(context):
        value=proposal(context,'research')
        value['changes'][0]['task_id']='Q'+str(max(int(t['task_id'][1:]) for t in context['tasks'])+1)
        value['coverage'][0]['remaining']='所要求的条件敏感性仍没有对应研究结果。'
        value['gap']=value['coverage'][0]['remaining']
        return value
    model=Model(*([missing]*limit));ctx=env.context(model);ctx.max_planning_calls=limit
    before=env.store.snapshot();executed=[]
    async def worker(branch,runtime):
        key=branch['subproblem']['id'];executed.append(key)
        assert submit(runtime.context.research,key,{'sample_count':8})[0]['published']
        return {'log':['sample count saved; coverage gap still open']}
    monkeypatch.setattr(control,'execute',worker)
    for _ in range(limit):
        state=observe(env,ctx);selected=loop.select(state,SimpleNamespace(context=ctx))
        assert selected[0].arg['capability']=='experiments.execute'
        run(loop.act(selected[0].arg,SimpleNamespace(context=ctx)))
        assert env.store.snapshot()['tasks'][executed[-1]]['acceptance']=='accepted'
    for _ in range(3):
        state=observe(env,ctx)
        assert state['research_blocked'] and loop.select(state,SimpleNamespace(context=ctx))==loop.END
        receipt=planner.decision_records(env.store)[0][1]
        assert receipt['status']=='limited' and receipt['blocked'] is True
        env.store.recover();loop.resume(env.store,planning_limit=ctx.max_planning_calls)
    after=env.store.snapshot()
    assert len(model.calls)==limit and after['planning_calls']==limit and len(executed)==limit
    assert all(after['tasks'][key]==task for key,task in before['tasks'].items())
    assert all(after['attempts'][key]==attempt for key,attempt in before['attempts'].items())
    assert all(after['evidence'][key]==evidence for key,evidence in before['evidence'].items())


def test_old_nonblocking_limit_receipt_blocks_until_explicit_valid_deliver(env):
    model=Model('deliver');ctx=env.context(model)
    assert env.store.reserve_planning_call('old-limit:first',limit=2)
    assert env.store.reserve_planning_call('old-limit:second',limit=2)
    key,_=planner.decision_context(env.store,env.store.snapshot())
    env.store.save_planning_decision(key,{'status':'limited','blocked':False})
    assert observe(env,ctx)['research_blocked'] and not model.calls
    assert env.store.planning_decision(key)['blocked'] is True
    loop.resume(env.store,planning_limit=ctx.max_planning_calls)
    state=observe(env,ctx)
    assert not state['research_blocked'] and len(model.calls)==1
    assert env.store.snapshot()['planning_calls']==3, 'Continuing uses remaining backend capacity, without resetting old usage'
    assert loop.select(state,SimpleNamespace(context=ctx)).arg['capability']=='writing.reconcile'


def test_cached_deliver_with_unanswered_requirement_cannot_unlock_writing(env):
    model=Model('deliver');ctx=env.context(model)
    key,context=planner.decision_context(env.store,env.store.snapshot())
    invalid=proposal(context);invalid['coverage'][0]['remaining']='尚未回答的研究目的。'
    env.store.save_planning_decision(key,{'status':'done','proposal':invalid})
    state=observe(env,ctx)
    assert state['research_blocked'] and loop.select(state,SimpleNamespace(context=ctx))==loop.END
    assert not model.calls and env.store.planning_decision(key)['status']=='planning_failed'


def test_accepted_tasks_without_current_evidence_do_not_skip_convergence(env):
    model=Model('deliver');ctx=env.context(model)
    with env.store._transaction() as db:
        snapshot=env.store._read(db)
        for evidence in snapshot['evidence'].values():evidence['active']=False
        env.store._save(db,snapshot,'controlled_inactive_products',{})
    state=observe(env,ctx)
    assert state['research_blocked'] and loop.select(state,SimpleNamespace(context=ctx))==loop.END
    assert not model.calls and env.store.snapshot()['planning_calls']==0


def test_pending_section_rewrite_cannot_bypass_missing_convergence_at_limit(env):
    model=Model('deliver');ctx=env.context(model);ctx.max_planning_calls=0
    env.store.report_issues([IssueReport(task_id='Q1',category='writing',section='evaluation',message='措辞尚需修订')],'sections')
    state=observe(env,ctx)
    assert state['research_blocked'] and loop.select(state,SimpleNamespace(context=ctx))==loop.END
    assert not model.calls and env.store.snapshot()['planning_calls']==0


def test_reserved_interrupted_planning_is_not_repeated_until_explicit_resume(env):
    model=Model('deliver');ctx=env.context(model)
    key,_=planner.decision_context(env.store,env.store.snapshot())
    assert env.store.reserve_planning_call(key)
    for _ in range(2):assert observe(env,ctx)['research_blocked']
    assert not model.calls and env.store.snapshot()['planning_calls']==1
    loop.resume(env.store)
    assert not observe(env,ctx)['research_blocked']
    assert len(model.calls)==1 and env.store.snapshot()['planning_calls']==2


@pytest.mark.parametrize('damage',['missing_basis','unknown_ref','fake_quote','unrelated_result','unknown_requirement',
                                   'revise_original','remove_requirement','relax_check','deliver_with_gap'])
def test_invalid_evidence_or_requirement_weakening_never_changes_research(env,damage):
    snapshot=env.store.snapshot();_,context=planner.decision_context(env.store,snapshot)
    value=proposal(context,'research')
    if damage=='missing_basis':value['basis']=[]
    elif damage=='unknown_ref':value['basis'][0]['ref']='R999'
    elif damage=='fake_quote':value['basis'][0]['quote']='unrecorded observed result'
    elif damage=='unrelated_result':
        unrelated=next(e for e in context['evidence'] if e.get('task_id')=='Q3')
        value['basis']=[{'ref':unrelated['id'],'quote':unrelated['text']}]
    elif damage=='unknown_requirement':value['requirement_ids']=['RQ999']
    elif damage=='revise_original':value['changes'][0].update(action='revise',task_id='Q1')
    elif damage=='remove_requirement':value['changes'][0]['requirement_ids']=[]
    elif damage=='relax_check':
        value['requirements']=copy.deepcopy(snapshot['spec']['requirements'])
        value['requirements'][1]['checks'][0]['expected']=999
    else:value={**proposal(context),'gap':'a remaining research gap'}
    with pytest.raises(ValueError):
        planner.validate_decision(planner.EvidenceDecision.model_validate(value),snapshot,context)
    assert env.store.snapshot()==snapshot


def test_historical_inactive_evidence_cannot_justify_new_research(env):
    snapshot=env.store.snapshot();_,context=planner.decision_context(env.store,snapshot)
    value=proposal(context,'research');ref=value['basis'][0]['ref']
    with env.store._transaction() as db:
        snapshot=env.store._read(db);snapshot['evidence'][ref]['active']=False
        env.store._save(db,snapshot,'controlled_invalidation',{})
    snapshot=env.store.snapshot();_,context=planner.decision_context(env.store,snapshot)
    with pytest.raises(ValueError,match='实际结果'):
        planner.validate_decision(planner.EvidenceDecision.model_validate(value),snapshot,context)


def add_passage(store):
    literature=LiteratureStore(store.root)
    source=literature.upsert({'title':'Estimate demand: representative sampling','url':'https://example.org/research',
                              'authors':['Known Author'],'provider':'controlled'})
    literature.add_passages(source,[{'text':'Estimate demand requires checking representative sampling and explicitly retaining uncertainty in the inference.',
                                    'locator':'原文第 1 段'}],'webpage',source['url'],'source-content')
    return source,literature.passages()[-1]


def test_literature_choice_invokes_registered_capability_then_redecides_from_actual_passage(env,monkeypatch):
    from ai_system.modules import literature as module
    from ai_system.modules.registry import CapabilityProduct
    def converge(context):
        value=proposal(context)
        passage=next(e for e in context['evidence'] if 'locator' in e)
        value['basis'].append({'ref':passage['id'],'quote':passage['text']})
        return value
    model=Model('literature',converge);ctx=env.context(model);before=env.store.snapshot();calls=[]
    state=observe(env,ctx);selected=loop.select(state,SimpleNamespace(context=ctx))
    assert selected.arg['capability']=='literature.collect'
    async def collect(ctx,inputs):
        calls.append(inputs['query']);source,_=add_passage(ctx.research)
        return CapabilityProduct('literature',{'search':{'status':'completed','source_ids':[source['id']],'errors':[]}})
    # The cached registry still dispatches through the real capability boundary.
    from ai_system.modules import registry
    monkeypatch.setattr(registry,'_registry',None)
    monkeypatch.setattr(module,'collect',collect)
    run(loop.act(selected.arg,SimpleNamespace(context=ctx)))
    state=observe(env,ctx)
    for _ in range(2):loop.resume(env.store);state=observe(env,ctx)
    assert len(calls)==1 and len(model.calls)==2
    assert any('locator' in e for e in model.calls[-1]['evidence'])
    assert env.store.snapshot()['tasks']==before['tasks'] and env.store.snapshot()['attempts']==before['attempts']
    assert loop.select(state,SimpleNamespace(context=ctx)).arg['capability']=='writing.reconcile'


def test_default_limit_allows_literature_then_research_then_explicit_deliver(env,monkeypatch):
    from ai_system.modules import literature as module,registry
    from ai_system.modules.registry import CapabilityProduct
    def targeted(context):
        value=proposal(context,'research')
        source=next(e for e in context['evidence'] if 'locator' in e)
        value['basis'].append({'ref':source['id'],'quote':source['text']})
        value['coverage'][0]['basis'].append({'ref':source['id'],'quote':source['text']})
        return value
    def converge(context):
        value=proposal(context)
        actual=[next(e for e in context['evidence'] if e.get('task_id')==key) for key in ('Q1','Q4')]
        value['coverage'][0].update(supported='; '.join(f"{e['name']}={e['text']}" for e in actual),
                                  basis=[{'ref':e['id'],'quote':e['text']} for e in actual],remaining='')
        return value
    model=Model('literature',targeted,converge);ctx=env.context(model);before=env.store.snapshot()
    assert ctx.max_planning_calls==DEFAULT_PLANNING_CALL_LIMIT>=3
    calls=[]
    async def collect(ctx,inputs):
        calls.append('literature.collect');source,_=add_passage(ctx.research)
        return CapabilityProduct('literature',{'search':{'status':'completed','source_ids':[source['id']],'errors':[]}})
    async def worker(branch,runtime):
        calls.append('experiments.execute')
        assert branch['subproblem']['id']=='Q4'
        assert submit(runtime.context.research,'Q4',{'sensitivity':0.2})[0]['published']
        return {'log':['targeted evidence registered']}
    monkeypatch.setattr(registry,'_registry',None)
    monkeypatch.setattr(module,'collect',collect)
    monkeypatch.setattr(control,'execute',worker)
    first=loop.select(observe(env,ctx),SimpleNamespace(context=ctx))
    assert first.arg['capability']=='literature.collect'
    run(loop.act(first.arg,SimpleNamespace(context=ctx)))
    second=loop.select(observe(env,ctx),SimpleNamespace(context=ctx))
    assert second[0].arg['capability']=='experiments.execute'
    run(loop.act(second[0].arg,SimpleNamespace(context=ctx)))
    for _ in range(3):
        state=observe(env,ctx)
        assert not state['research_blocked']
        assert loop.select(state,SimpleNamespace(context=ctx)).arg['capability']=='writing.reconcile'
        env.store.recover();loop.resume(env.store,planning_limit=ctx.max_planning_calls)
    assert calls==['literature.collect','experiments.execute'] and len(model.calls)==3
    after=env.store.snapshot()
    assert after['planning_calls']==3
    assert all(after['tasks'][key]==task for key,task in before['tasks'].items())
    assert all(after['attempts'][key]==attempt for key,attempt in before['attempts'].items())
    assert all(after['evidence'][key]==evidence for key,evidence in before['evidence'].items())


@pytest.mark.parametrize('status',['failed','completed'])
def test_literature_failure_or_metadata_only_result_never_claims_evidence_or_repeats(env,monkeypatch,status):
    model=Model('literature');ctx=env.context(model);before=env.store.snapshot();calls=[]
    state=observe(env,ctx);selected=loop.select(state,SimpleNamespace(context=ctx))
    async def collect(ctx,name,**inputs):
        calls.append(name)
        return {'search':{'status':status,'source_ids':[],'errors':[]}}
    monkeypatch.setattr(loop,'invoke',collect)
    run(loop.act(selected.arg,SimpleNamespace(context=ctx)))
    for _ in range(3):
        loop.resume(env.store)
        state=observe(env,ctx)
        assert state['research_blocked'] and loop.select(state,SimpleNamespace(context=ctx))==loop.END
    assert calls==['literature.collect'] and len(model.calls)==1
    assert env.store.snapshot()['attempts']==before['attempts'] and env.store.snapshot()['tasks']==before['tasks']
    assert any('缺口尚未解决' in str(content) for _,content in env.events)


def test_interrupted_collection_reuses_saved_completed_operation_without_network(env):
    model=Model('literature','deliver');ctx=env.context(model)
    state=observe(env,ctx);action=loop.select(state,SimpleNamespace(context=ctx)).arg
    planner.begin_collection(env.store,action['decision_key'])
    literature=LiteratureStore(env.store.root)
    operation,record=literature.operation('search',action['inputs']['query'])
    source,_=add_passage(env.store)
    literature.finish(operation,{**record,'status':'completed','source_ids':[source['id']],'errors':[]})
    env.store.recover();loop.resume(env.store)
    state=observe(env,ctx)
    assert not state['research_blocked'] and len(model.calls)==2
    assert planner.literature_action(env.store) is None
    assert env.store.planning_decision(action['decision_key'])['status']=='collected'


def test_uncertain_collection_is_preserved_and_never_blindly_reissued(env):
    model=Model('literature');ctx=env.context(model)
    state=observe(env,ctx);action=loop.select(state,SimpleNamespace(context=ctx)).arg
    planner.begin_collection(env.store,action['decision_key'])
    for _ in range(3):
        loop.resume(env.store)
        state=observe(env,ctx)
        assert state['research_blocked'] and loop.select(state,SimpleNamespace(context=ctx))==loop.END
    assert len(model.calls)==1 and planner.literature_action(env.store) is None
    assert env.store.planning_decision(action['decision_key'])['status']=='collecting'


def test_new_relevant_source_reopens_decision_but_writing_commits_do_not(env):
    model=Model('deliver','deliver');ctx=env.context(model)
    observe(env,ctx)
    env.store.save_sections({'abstract':'已保存的草稿。'},[])
    observe(env,ctx)
    assert len(model.calls)==1
    add_passage(env.store)
    observe(env,ctx)
    assert len(model.calls)==2


def test_literature_off_is_respected(env):
    literature=LiteratureStore(env.store.root);literature.set_policy('off')
    _,context=planner.decision_context(env.store,env.store.snapshot())
    with pytest.raises(ValueError,match='关闭'):
        planner.validate_decision(planner.EvidenceDecision.model_validate(proposal(context,'literature')),env.store.snapshot(),context)
