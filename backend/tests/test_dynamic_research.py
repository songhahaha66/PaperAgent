"""The single controller chooses work from actual state; no network replies."""
import asyncio
import copy
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from ai_system.graph.context import GraphContext
from ai_system.graph.build import compile_graph
from ai_system.research import control,loop,planner
from ai_system.research.models import WorkerResult,IssueReport,PlanPatch,TaskChange,is_continuation
from ai_system.research.store import ResearchStore,digest

SOURCE='Estimate demand and assess costs.'


@pytest.fixture
def study(tmp_path):
    analysis={'title':'Demand and costs','overall_analysis':'Investigate the original questions.',
        'subproblems':[{'id':'Q1','kind':'compute','title':'Demand','question':'Estimate demand','depends_on':[],'deliverables':[]},
                       {'id':'Q2','kind':'compute','title':'Costs','question':'assess costs','depends_on':[],'deliverables':[]}],
        'requirements':[{'id':'RQ1','statement':'Estimate demand','source_quote':'Estimate demand','kind':'goal','task_ids':['Q1'],'checks':[]},
                        {'id':'RQ2','statement':'assess costs','source_quote':'assess costs','kind':'goal','task_ids':['Q2'],'checks':[]}],
        'assumptions':[]}
    store=ResearchStore(tmp_path,'dynamic');store.initialize(SOURCE,analysis)
    ctx=GraphContext.create(str(tmp_path),planner_llm=None);ctx.research=store
    return SimpleNamespace(store=store,ctx=ctx,state={'task':SOURCE,'analysis':analysis,'data_profile':'Supplied source material.'})


def coverage(context):
    rows=[]
    for requirement in context['spec']['requirements']:
        actual=[e for e in context['evidence'] if e.get('task_id') in requirement['task_ids']]
        rows.append({'requirement_id':requirement['id'],'requirement':requirement['statement'],
                     'supported':'; '.join(e['name']+'='+e['text'] for e in actual),
                     'basis':[{'ref':e['id'],'quote':e['text']} for e in actual],
                     'remaining':'' if actual else 'No actual answer yet.'})
    return rows


def decision(context,action='execute',**fields):
    rows=coverage(context)
    value={'action':action,'reason':'Choose the next useful action against the original question.',
           'requirement_ids':['RQ1'],'coverage':rows,'basis':[],'gap':'Resolve the remaining question.'}
    if action=='execute':
        task=next(t for t in context['tasks'] if t['task_id']==context['ready_tasks'][0])
        value.update(task_ids=[task['task_id']],requirement_ids=task['requirement_ids'])
    if action=='deliver':
        basis=[b for row in rows for b in row['basis']]
        value.update(requirement_ids=[r['id'] for r in context['spec']['requirements']],gap='',basis=basis,
                     outline=[{'key':'custom:findings','title':'Measured findings and boundaries',
                               'purpose':'Answer the original questions with actual evidence and sources.',
                               'task_ids':[t['task_id'] for t in context['tasks'] if not t['retired']], 'basis':basis}])
    value.update(fields)
    return planner.EvidenceDecision.model_validate(value)


class Model:
    def __init__(self,*steps):self.steps=list(steps);self.contexts=[]
    def with_structured_output(self,schema,**kwargs):assert schema is planner.EvidenceDecision;return self
    async def ainvoke(self,messages):
        context=json.loads(messages[-1].content);self.contexts.append(context)
        step=self.steps.pop(0)
        return step(context) if callable(step) else decision(context,step)


def observe(study):
    update=asyncio.run(control.observe(study.state,SimpleNamespace(context=study.ctx)))
    study.state={**study.state,**update};return update


def publish(study,key,value):
    order=study.store.claim(key)
    run=study.ctx.fs.create_run();study.ctx.fs.save_run_stdout(run,str(value));study.ctx.fs.finish_run(run,'success')
    result=study.ctx.registry.record_value(key,'answer_'+key,str(value),run)
    report={'status':'completed','summary':'Actual registered result.',
            'requirement_evidence':[{'requirement_id':rid,'evidence_refs':[result.id]} for rid in study.store.snapshot()['tasks'][key]['requirement_ids']]}
    return study.store.commit(WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',report=report,results=[asdict(result)]))


def test_automatic_entry_has_one_controller_and_can_start_without_results(study):
    nodes=compile_graph().get_graph().nodes
    assert 'observe' in nodes and 'act' in nodes and 'design' not in nodes and 'confirm' not in nodes
    from ai_system.modules.registry import capabilities
    names={c['name'] for c in capabilities().describe()}
    assert 'experiments.plan' not in names and 'experiments.execute' in names
    study.ctx.planner_llm=Model('execute')
    state=observe(study);selected=loop.select(state,SimpleNamespace(context=study.ctx))
    assert not state['research_blocked'] and len(selected)==1
    assert selected[0].arg['inputs']['branch']['subproblem']['id']=='Q1'
    assert study.ctx.planner_llm.contexts[0]['evidence']==[]
    assert selected[0].arg['inputs']['branch']['source']==SOURCE


def test_ordinary_solver_does_not_generate_candidates_or_hide_design_failures(study,monkeypatch):
    from ai_system.modules.experiments import execution
    captured=[]
    async def solve(ctx,branch,runs,messages):
        captured.append((branch,messages));raise RuntimeError('stop before any model or kernel')
    async def forbidden(*args,**kwargs):pytest.fail('ordinary execution must not invoke candidate generation')
    from ai_system.modules import registry
    monkeypatch.setattr(registry,'invoke',forbidden);monkeypatch.setattr(execution,'_solve_loop',solve)
    branch={'source':SOURCE,'subproblem':study.state['analysis']['subproblems'][0],
            'analysis':study.state['analysis'],'data_profile':'Known materials.'}
    with pytest.raises(RuntimeError,match='stop before'):
        asyncio.run(execution.solve(branch,SimpleNamespace(context=study.ctx)))
    assert captured[0][0]['design']['direct_execution']
    assert SOURCE in captured[0][1][0].content


def test_failed_method_can_change_without_replaying_other_accepted_work(study,monkeypatch):
    assert publish(study,'Q2',12)['published']
    order=study.store.claim('Q1')
    study.store.commit(WorkerResult(order=order,execution_status='failed',acceptance='inconclusive',
        report={'status':'failed','error':'The selected estimator failed to fit.'},
        issues=[IssueReport(task_id='Q1',category='execution',message='The selected estimator failed to fit.')]))
    before=study.store.snapshot();kept=copy.deepcopy(before['tasks']['Q2'])
    def repair(context):
        assert any(a['execution_status']=='failed' and 'failed to fit' in a['error'] for a in context['attempts'])
        return decision(context,'research',changes=[{'task_id':'Q1','objective':'Estimate demand',
                         'method_instruction':'Use a different estimator appropriate to the supplied data.'}])
    study.ctx.planner_llm=Model(repair,'execute','deliver')
    assert not observe(study)['research_blocked']
    changed=study.store.snapshot()
    assert changed['tasks']['Q1']['objective']=='Estimate demand' and changed['tasks']['Q1']['revision']==2
    assert changed['tasks']['Q2']==kept and changed['attempts'][order.attempt_id]['execution_status']=='failed'
    state=observe(study);action=loop.select(state,SimpleNamespace(context=study.ctx))[0].arg
    assert action['task_id']=='Q1' and 'different estimator' in action['inputs']['branch']['feedback']
    calls=[]
    async def worker(ctx,name,**inputs):
        assert name=='experiments.execute';calls.append(inputs['branch']['subproblem']['id'])
        assert publish(study,'Q1',8)['published'];return {'log':['measured']}
    monkeypatch.setattr(loop,'invoke',worker)
    asyncio.run(loop.act(action,SimpleNamespace(context=study.ctx)))
    asyncio.run(loop.act(action,SimpleNamespace(context=study.ctx)))
    state=observe(study)
    assert calls==['Q1'] and not state['research_blocked']
    assert study.store.snapshot()['tasks']['Q2']==kept
    assert loop.select(state,SimpleNamespace(context=study.ctx)).arg['capability']=='writing.reconcile'
    assert state['outline'][0]['key']=='custom:findings'
    from ai_system.modules.writing.sections import section_request,section_plan
    request=section_request('custom:findings','Measured findings',study.state,study.ctx)
    assert 'actual evidence and sources' in request and '{{R:' in request
    assert [key for key,_,_ in section_plan(study.state)]==['custom:findings','references']
    attempts=copy.deepcopy(study.store.snapshot()['attempts'])
    study.store.recover();loop.resume(study.store);observe(study)
    assert study.store.snapshot()['attempts']==attempts and len(study.ctx.planner_llm.contexts)==3


def test_stopping_a_branch_does_not_answer_or_delete_its_original_requirement(study):
    before=study.store.snapshot()
    study.store.apply_patch(PlanPatch(command_id='stop',base_revision=1,reason='This attempt has no useful route.',
        changes=[TaskChange(action='retire',task_id='Q1',objective='Estimate demand')]))
    after=study.store.snapshot()
    assert after['tasks']['Q1']['retired'] and after['spec']==before['spec']
    assert not ResearchStore.delivery_ready(after)
    _,context=planner.decision_context(study.store,after)
    with pytest.raises(ValueError):planner.validate_decision(decision(context,'deliver'),after,context)
    assert coverage(context)[0]['remaining']


def test_writing_coverage_gap_returns_to_controller_without_invalidating_correct_results(study):
    publish(study,'Q1',8);publish(study,'Q2',12)
    before=study.store.snapshot()
    [issue]=study.store.report_issues([IssueReport(task_id='Q1',category='coverage',section='solve:Q1',
        message='原问题仍有缺少实际依据的部分，请控制器核对，而不是改写成已完成。')],'sections')
    assert not issue['blocking']
    study.store.schedule_investigations();study.store.recover()
    after=study.store.snapshot()
    assert after['tasks']==before['tasks'] and after['attempts']==before['attempts']
    _,context=planner.decision_context(study.store,after)
    assert any(i['id']==issue['id'] for i in context['issues'])
    assert all(e['id']!=issue['id'] for e in context['evidence'])


def test_initial_material_actions_and_questions_need_no_fabricated_basis(study):
    _,context=planner.decision_context(study.store,study.store.snapshot())
    for action,fields in [('inspect',{}),('ask',{'question':'Which supplied column is the requested target?'}),
                          ('literature',{'query':'Demand estimation assumptions'})]:
        value=decision(context,action,**fields)
        planner.validate_decision(value,study.store.snapshot(),context)
        assert not value.basis


def test_user_clarification_reaches_the_selected_worker_without_rewriting_the_goal(study):
    from ai_system.modules.experiments.execution import build_task
    before=copy.deepcopy(study.store.snapshot()['spec'])
    study.store.save_planning_decision('research-next:question',{'status':'replied',
        'proposal':{'action':'ask','question':'Which input is the demand measure?','requirement_ids':['RQ1']},
        'response':'Use the supplied measured_demand column.'})
    study.ctx.planner_llm=Model('execute')
    action=loop.select(observe(study),SimpleNamespace(context=study.ctx))[0].arg
    branch=action['inputs']['branch']
    assert branch['clarifications'][0]['ref']=='research-next:question'
    chosen={'name':'Use supplied observations','idea':'Use actual inputs','formulation':'','method_keys':[],
            'steps':['read inputs'],'validation':'fixed checks'}
    text=build_task({**branch,'design':{'chosen':chosen}},study.ctx)
    assert 'measured_demand' in text and '不是计算成果' in text
    assert study.store.snapshot()['spec']==before


def test_user_revision_without_scope_only_invalidates_its_actual_target(study):
    from ai_system.research.models import Requirement
    publish(study,'Q1',8);publish(study,'Q2',12)
    before=study.store.snapshot();change='预测范围改为下周'
    requirement=Requirement(id='RQ3',statement=change,source_quote=change,kind='goal',task_ids=['Q1'])
    patch=planner.propose_revision(before,'range-change',1,change,None,[requirement])
    study.store.apply_revision_command('range-change',1,change,None,patch)
    after=study.store.snapshot()
    assert after['tasks']['Q2']==before['tasks']['Q2']
    assert after['tasks']['Q1']['validity']=='stale' and after['spec']['requirement']==SOURCE
    assert after['spec']['requirements'][:2]==before['spec']['requirements']


def test_old_revision_receipt_remains_idempotent_without_optional_method_metadata(study):
    patch=PlanPatch(command_id='old-revision',base_revision=1,reason='Use all original observations.',
                    changes=[TaskChange(task_id='Q1',objective='Estimate demand using all original observations')])
    old_payload=patch.model_dump()
    for change in old_payload['changes']:change.pop('method_instruction')
    receipt=study.store.apply_patch(PlanPatch.model_validate(old_payload));before=study.store.snapshot()
    with study.store._connect() as db:
        stored=db.execute('SELECT digest FROM commands WHERE research_id=? AND id=?',('dynamic','old-revision')).fetchone()[0]
    assert stored==digest(old_payload)
    assert study.store.apply_patch(PlanPatch.model_validate(old_payload))==receipt
    assert study.store.snapshot()==before


def test_main_loop_has_no_four_decision_cutoff_and_completed_inspections_are_not_replayed(study,monkeypatch):
    study.ctx.planner_llm=Model(*(['inspect']*6),'pause')
    calls=[]
    async def inspect(ctx,name,**inputs):
        assert name=='experiments.inspect';calls.append(name)
        return {'inputs':study.store.file_inputs(),'profile':'Observed material version '+str(len(calls))}
    monkeypatch.setattr(loop,'invoke',inspect)
    for _ in range(6):
        state=observe(study);action=loop.select(state,SimpleNamespace(context=study.ctx)).arg
        asyncio.run(loop.act(action,SimpleNamespace(context=study.ctx)))
        asyncio.run(loop.act(action,SimpleNamespace(context=study.ctx)))
    assert observe(study)['research_blocked']
    assert observe(study)['research_blocked'] and len(study.ctx.planner_llm.contexts)==7
    assert len(calls)==6 and study.store.snapshot()['planning_calls']==7
    assert study.store.snapshot()['outcome']=='blocked' and not study.store.snapshot()['attempts']


@pytest.mark.parametrize('message',['继续','恢复','请继续执行','resume'])
def test_pure_resume_from_stale_revision_mode_never_mutates_goals(study,message):
    from services.chat_services.research_session import resolve_action
    snapshot=SimpleNamespace(graph_status='done',work_status='failed',has_research=True,engine_version='research-v3',
                             research_id='dynamic',tasks=list(study.store.snapshot()['tasks'].values()))
    assert resolve_action('revise_research',message,snapshot)=='resume'
    before=study.store.snapshot()
    with pytest.raises(ValueError):planner.propose_revision(before,'bad',1,message,None,[])
    with pytest.raises(ValueError):study.store.apply_revision_command('bad',1,message,None,None)
    assert study.store.snapshot()==before
    mixed='继续，并将预测范围改为下周'
    assert not is_continuation(mixed) and resolve_action('revise_research',mixed,snapshot)=='revise_research'
    assert resolve_action('auto',mixed,snapshot)=='revise_research'
    assert resolve_action('auto','解释一下已有结果',snapshot)=='ask'
    assert resolve_action('auto','修改后的结果是什么？',snapshot)=='ask'


def test_user_template_keeps_its_structure_while_using_the_evidence_outline(study,monkeypatch):
    from ai_system.modules.writing import sections
    publish(study,'Q1',8);publish(study,'Q2',12)
    study.ctx.planner_llm=Model('deliver');observe(study)
    study.ctx.output_mode='latex';study.ctx.template_requirements='- L2 第1行: 摘要\n- L2 第2行: 模型建立与求解\n- L2 第3行: 结论'
    calls=[]
    async def many(ctx,state,items):
        calls.extend((key,sections.section_request(key,title,state,ctx)) for key,title in items)
        return {key:r'原生正文 '+key for key,_ in items}
    monkeypatch.setattr(sections,'write_many',many)
    result=asyncio.run(sections.write(study.state,SimpleNamespace(context=study.ctx)))
    assert [s['title'] for s in result['outline']][:2]==['摘要','模型建立与求解']
    assert any('原生 LaTeX' in sections.writing_style(study.ctx) for _ in calls)
    assert any('{{R:' in prompt for _,prompt in calls)


def test_intermediate_actions_need_goal_and_parameters_without_full_coverage(study):
    snapshot=study.store.snapshot();_,context=planner.decision_context(study.store,snapshot)
    proposal=planner.EvidenceDecision(action='execute',reason='Compute the unresolved demand estimate.',
        requirement_ids=['RQ1'],task_ids=['Q1'])
    planner.validate_decision(proposal,snapshot,context)
    with pytest.raises(ValueError,match='原要求'):
        planner.validate_decision(proposal.model_copy(update={'requirement_ids':['unknown']}),snapshot,context)
    with pytest.raises(ValueError,match='就绪'):
        planner.validate_decision(proposal.model_copy(update={'task_ids':['unknown']}),snapshot,context)
    incomplete=planner.EvidenceDecision(action='deliver',reason='Write findings.',requirement_ids=['RQ1','RQ2'],
        outline=[{'key':'abstract','title':'Findings','purpose':'Answer the original question.'}])
    with pytest.raises(ValueError,match='逐项'):
        planner.validate_decision(incomplete,snapshot,context)


def test_technical_review_failure_keeps_controller_frontier_and_completed_steps(study,monkeypatch):
    publish(study,'Q1',3);publish(study,'Q2',4)
    key,context=planner.decision_context(study.store,study.store.snapshot())
    proposal=decision(context,'deliver')
    study.store.save_planning_decision(key,{'status':'done','proposal':proposal.model_dump()})
    with study.store._transaction() as db:
        snapshot=study.store._read(db);snapshot['workflow']={'epoch':key,'done':['reconcile','write'],'repairs':0,'blocked':True}
        study.store._save(db,snapshot,'fixture_review_interrupted',{})
    study.store.report_issues([IssueReport(task_id='Q1',category='service',message='Review transport interrupted.')],'review:sections')
    after,_=planner.decision_context(study.store,study.store.snapshot());assert after==key
    assert planner.current_decision(study.store)
    planner.validate_decision(proposal,study.store.snapshot(),context)
    assert not study.store.delivery_ready(study.store.snapshot())
    loop.resume(study.store)
    assert loop.flow(study.store)['done']==['reconcile','write']
    selected=loop.select({**study.state,'research_blocked':False},SimpleNamespace(context=study.ctx))
    assert selected.arg['capability']=='writing.validate'
