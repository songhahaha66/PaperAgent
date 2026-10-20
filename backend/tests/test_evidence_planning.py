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
from test_paper_graph import decision_outline
from ai_system.research.models import TaskChange,IssueReport
from ai_system.research.literature import LiteratureStore
from test_native_latex import accepted_store
from test_research_p2 import problem,TASK,submit
from test_review_cache import no_network


def run(value):return asyncio.run(value)


def proposal(context, action='deliver'):
    result=next((e for e in context['evidence'] if e.get('task_id')=='Q1'),None)
    coverage=[]
    for requirement in context['spec']['requirements']:
        actual=next((e for task in reversed(requirement['task_ids']) for e in context['evidence'] if e.get('task_id')==task),None)
        coverage.append({'requirement_id':requirement['id'],'requirement':requirement['statement'],
            'supported':f"{actual['name']}={actual['text']}" if actual else '',
            'basis':[{'ref':actual['id'],'quote':actual['text']}] if actual else [],
            'remaining':'' if actual else '由既有写作和导出阶段实施。'})
    value={'action':action,'reason':'已有估计提供了实际依据，按原要求选择下一步。',
           'requirement_ids':[r['id'] for r in context['spec']['requirements']] if action=='deliver' else ['RQ1'],
           'basis':[{'ref':result['id'],'quote':result['text']}] if result else [],'coverage':coverage}
    if action=='deliver':value['outline']=decision_outline(context,coverage)
    if action=='pause':value['gap']='没有可用研究证据，保留当前材料与缺口。'
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
    with store._transaction() as db:
        saved=store._read(db);saved['materials']={'inputs':store.file_inputs(),'profile':''};store._save(db,saved,'materials_profiled',{})
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


def test_sufficient_evidence_and_repeat_observe_resume_do_not_spend_or_add_work(env):
    model=Model('deliver');ctx=env.context(model);before=env.store.snapshot()
    state=observe(env,ctx)
    for _ in range(3):
        env.store.recover();loop.resume(env.store);state=observe(env,ctx)
    assert len(model.calls)==1 and env.store.snapshot()['planning_calls']==1
    assert env.store.snapshot()['tasks']==before['tasks']
    assert env.store.snapshot()['attempts']==before['attempts']
    assert loop.select(state,SimpleNamespace(context=ctx)).arg['capability']=='writing.reconcile'


def test_deliver_tool_schema_requires_coverage_outline_and_no_execution_tasks():
    from langchain_core.utils.function_calling import convert_to_openai_tool
    schema=convert_to_openai_tool(planner.EvidenceDecision)['function']['parameters']
    deliver=next(v for v in schema['anyOf'] if v['properties']['action']['enum']==['deliver'])
    assert set(deliver['required'])=={'coverage','outline'}
    assert deliver['properties']['coverage']['minItems']==deliver['properties']['outline']['minItems']==1
    assert deliver['properties']['task_ids']['maxItems']==0


def test_incomplete_deliver_repairs_once_without_rerunning_accepted_tasks(env,monkeypatch):
    from langchain_core.messages import AIMessage
    from ai_system.graph import llm_utils
    monkeypatch.setattr(llm_utils,'RETRY_DELAY',0)
    before=env.store.snapshot();_,context=planner.decision_context(env.store,before)
    valid=proposal(context)
    class Incomplete(Model):
        def __init__(self):self.calls=0
        async def ainvoke(self,messages):
            self.calls+=1
            if self.calls==1:
                return planner.EvidenceDecision(action='deliver',reason='已有结果可以交付',
                    requirement_ids=valid['requirement_ids'],task_ids=['Q1','Q2'])
            assert self.calls==2 and '逐项核对' in messages[-1].content
            assert json.loads(messages[-2].content)['task_ids']==['Q1','Q2']
            return AIMessage(content=json.dumps(valid))
    model=Incomplete();ctx=env.context(model)
    state=observe(env,ctx)
    assert not state['research_blocked'] and model.calls==2
    assert env.store.snapshot()['attempts']==before['attempts']
    assert env.store.snapshot()['tasks']==before['tasks']
    assert loop.select(state,SimpleNamespace(context=ctx)).arg['capability']=='writing.reconcile'


def test_decision_context_uses_products_and_purposes_without_completion_shortcuts(env):
    snapshot=env.store.snapshot()
    for task in snapshot['tasks'].values():
        task['report'].update(summary='all requirements are covered by accepted tasks',
                              key_findings=['unsubstantiated completion claim'],limitations=['Only the stated conditions were examined.'])
    key,context=planner.decision_context(env.store,snapshot)
    assert all('report' not in task and 'active_attempt' not in task for task in context['tasks'])
    assert all('execution_status' in task and 'acceptance' in task for task in context['tasks'])
    assert 'all requirements are covered by accepted tasks' not in json.dumps(context)
    assert 'unsubstantiated completion claim' not in json.dumps(context)
    assert all(task['limitations']==['Only the stated conditions were examined.'] for task in context['tasks'])
    assert context['spec']['requirements']==snapshot['spec']['requirements']
    assert next(t['objective'] for t in context['tasks'] if t['task_id']=='Q1')==snapshot['tasks']['Q1']['objective']
    assert {'demand','allocation','residual','independent'}<={e['name'] for e in context['evidence']}
    revised=copy.deepcopy(snapshot);revised['tasks']['Q1']['revision']+=1
    new_key,new_context=planner.decision_context(env.store,revised)
    assert new_context!=context and new_key!=key, 'A changed attempt frontier requires a new action decision, not a completion inference'


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
        assert all('report' not in t for t in context['tasks'])
        value=proposal(context,'research')
        value['gap']='现有估计未回答在题目所述不同条件下是否仍适用。'
        value['coverage'][0]['remaining']=value['gap']
        value['changes'][0]['objective']='检验现有估计在题目所述条件间是否保持适用，报告实际变化及适用边界。'
        return value
    model=Model(missing_part);ctx=env.context(model)
    state=observe(env,ctx)
    assert not state['research_blocked'] and len(model.calls)==1
    selected=loop.select(state,SimpleNamespace(context=ctx))
    assert selected=='observe', 'After a method/plan change, the same controller selects execution separately'
    assert env.store.snapshot()['tasks']['Q4']['acceptance']=='pending'
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


def test_backend_allocation_preserves_existing_work_and_only_dispatches_new_tasks(env):
    before=env.store.snapshot()
    def next_work(context):
        return {'action':'execute','reason':'补充检验稳定性','requirement_ids':['RQ1'],
                'task_ids':['T1','T2'], 'changes':[
                    {'action':'add','task_id':key,'objective':'检验估计稳定性',
                     'dependencies':['Q1'],'requirement_ids':['RQ1']} for key in ('T1','T2')]}
    model=Model(next_work);ctx=env.context(model)
    state=observe(env,ctx)
    actions=loop.select(state,SimpleNamespace(context=ctx))
    assert not state['research_blocked'] and len(model.calls)==1
    assert [a.arg['task_id'] for a in actions]==['Q4','Q5']
    after=env.store.snapshot()
    assert all(after['tasks'][key]==task for key,task in before['tasks'].items())
    assert after['attempts']==before['attempts']
    assert all(after['tasks'][key]['dependencies']==['Q1'] for key in ('Q4','Q5'))


@pytest.mark.parametrize('aliases', [('T1','T1'), ('Q1','T1')])
def test_ambiguous_new_task_aliases_do_not_redirect_existing_evidence(env, aliases):
    snapshot=env.store.snapshot();_,context=planner.decision_context(env.store,snapshot)
    value=proposal(context,'research')
    value['changes']=[{**value['changes'][0],'task_id':key} for key in aliases]
    with pytest.raises(ValueError,match='临时标识'):
        planner.prepare_decision(planner.EvidenceDecision.model_validate(value),snapshot,context)
    assert env.store.snapshot()==snapshot


def test_failed_decision_message_keeps_actual_accepted_progress(env):
    from ai_system.graph.agent import PaperGraphAgent
    ctx=env.context(Model(TimeoutError('规划请求超时')))
    state=observe(env,ctx)
    message=PaperGraphAgent._final_message(state,ctx)
    accepted=sum(t['acceptance']=='accepted' and t['validity']=='current' for t in env.store.snapshot()['tasks'].values())
    assert '规划请求超时' in message and f'已保留 {accepted} 项已验收任务' in message
    assert '存在未关闭' not in message and '尚未开始实验计算' not in message


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
    model=Model('pause');ctx=env.context(model)
    with env.store._transaction() as db:
        snapshot=env.store._read(db)
        for evidence in snapshot['evidence'].values():evidence['active']=False
        env.store._save(db,snapshot,'controlled_inactive_products',{})
    state=observe(env,ctx)
    assert state['research_blocked'] and loop.select(state,SimpleNamespace(context=ctx))==loop.END
    assert len(model.calls)==1 and env.store.snapshot()['planning_calls']==1


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


@pytest.mark.parametrize('damage',['unknown_ref','fake_quote','unrelated_result','unknown_requirement',
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


def test_intermediate_action_may_omit_coverage_but_declared_evidence_remains_checked(env):
    snapshot=env.store.snapshot();_,context=planner.decision_context(env.store,snapshot)
    value=proposal(context,'research');row=value.pop('coverage')[0];value.pop('basis')
    planner.validate_decision(planner.EvidenceDecision.model_validate(value),snapshot,context)
    value['coverage']=[row]  # A partial coverage statement is optional mid-research.
    planner.validate_decision(planner.EvidenceDecision.model_validate(value),snapshot,context)
    row['basis'][0]['ref']='R999'
    with pytest.raises(ValueError,match='实际结果'):
        planner.validate_decision(planner.EvidenceDecision.model_validate(value),snapshot,context)


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


@pytest.mark.parametrize('status',['failed','completed'])
def test_literature_failure_or_metadata_only_result_never_claims_evidence_or_repeats(env,monkeypatch,status):
    model=Model('literature','pause');ctx=env.context(model);before=env.store.snapshot();calls=[]
    state=observe(env,ctx);selected=loop.select(state,SimpleNamespace(context=ctx))
    async def collect(ctx,name,**inputs):
        calls.append(name)
        return {'search':{'status':status,'source_ids':[],'errors':[]}}
    monkeypatch.setattr(loop,'invoke',collect)
    run(loop.act(selected.arg,SimpleNamespace(context=ctx)))
    for _ in range(3):
        state=observe(env,ctx)
        assert state['research_blocked'] and loop.select(state,SimpleNamespace(context=ctx))==loop.END
    assert calls==['literature.collect'] and len(model.calls)==2
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
