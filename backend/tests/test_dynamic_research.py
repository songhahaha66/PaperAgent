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
from ai_system.research.models import WorkerResult, IssueReport
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






def observe(study):
    update=asyncio.run(control.observe(study.state,SimpleNamespace(context=study.ctx)))
    study.state={**study.state,**update};return update


def publish(study,key,value):
    order=study.store.claim(key)
    run=study.ctx.fs.create_run();study.ctx.fs.save_run_stdout(run,str(value));study.ctx.fs.finish_run(run,'success')
    result=study.ctx.registry.record_value(key,'answer_'+key,str(value),run)
    report={'status':'completed','summary':'Actual registered result.',
            'requirement_evidence':[{'requirement_id':rid,'evidence_refs':[result.id]} for rid in study.store.snapshot()['experiments'][key]['requirement_ids']]}
    return study.store.commit(WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',report=report,results=[asdict(result)]))




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












def test_user_revision_without_scope_preserves_results_and_updates_only_its_question(study):
    from ai_system.research.models import Requirement
    publish(study,'Q1',8);publish(study,'Q2',12)
    current=study.store.snapshot()
    results=study.store.registry_payload()['results']
    loop._save_assessments(study.store,planner.NextAction(action='observe',reason='记录已有问题答案',judgments=[
        planner.Judgment(coverage='answered',stop_reason='已回答本次给定样本范围，推广证据仍未知',question_id=current['experiments'][key]['question_id'],
            statement='当前数据下的结果 {{R:'+next(r['id'] for r in results if r['subproblem']==key)+'}}',
            artifact_refs=[r['id'] for r in results if r['subproblem']==key],limitations=['仅限已执行条件'])
        for key in ('Q1','Q2')]))
    before=study.store.snapshot();change='预测范围改为下周'
    requirement=Requirement(id='RQ3',statement=change,source_quote=change,kind='goal',task_ids=['Q1'])
    patch=planner.propose_revision(before,'range-change',1,change,None,[requirement])
    study.store.apply_revision_command('range-change',1,change,None,patch)
    after=study.store.snapshot()
    assert after['experiments']==before['experiments']
    assert after['executions']==before['executions'] and after['artifacts']==before['artifacts']
    q1=before['experiments']['Q1']['question_id'];q2=before['experiments']['Q2']['question_id']
    assert after['questions'][q1]['answer_status']=='needs_update'
    assert after['questions'][q2]==before['questions'][q2]
    assert after['spec']['requirement']==SOURCE
    assert after['spec']['requirements'][:2]==before['spec']['requirements']






def test_revision_requires_a_structured_patch_without_guessing_text(study):
    before=study.store.snapshot()
    with pytest.raises(ValueError):planner.propose_revision(before,'bad',1,'继续',None,[])
    with pytest.raises(ValueError):study.store.apply_revision_command('bad',1,'继续',None,None)
    assert study.store.snapshot()==before
