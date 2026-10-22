"""The single controller chooses work from actual state; no network replies."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from ai_system.graph.context import GraphContext
from ai_system.research import planner
from ai_system.research.models import WorkerResult
from ai_system.research.store import ResearchStore

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


def publish(study,key,value):
    order=study.store.claim(key)
    run=study.ctx.fs.create_run();study.ctx.fs.save_run_stdout(run,str(value));study.ctx.fs.finish_run(run,'success')
    result=study.ctx.registry.record_value(key,'answer_'+key,str(value),run)
    report={'status':'completed','summary':'Actual registered result.',
            'requirement_evidence':[{'requirement_id':rid,'evidence_refs':[result.id]} for rid in study.store.snapshot()['experiments'][key]['requirement_ids']]}
    return study.store.commit(WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',report=report,results=[asdict(result)]))


def test_user_revision_without_scope_preserves_results_and_updates_only_its_question(study):
    from ai_system.research.models import Requirement
    publish(study,'Q1',8);publish(study,'Q2',12)
    current=study.store.snapshot()
    results=study.store.registry_payload()['results']
    from research_tools_helpers import answer
    for key in ('Q1','Q2'):
        answer(study.store,question_id=current['experiments'][key]['question_id'],coverage='answered',
            stop_reason='已回答本次给定样本范围，推广证据仍未知',
            statement='当前数据下的结果 {{R:'+next(r['id'] for r in results if r['subproblem']==key)+'}}',
            artifact_refs=[r['id'] for r in results if r['subproblem']==key],limitations=['仅限已执行条件'])

    before=study.store.snapshot();change='预测范围改为下周'
    requirement=Requirement(id='RQ3',statement=change,source_quote=change,kind='goal',task_ids=['Q1'])
    patch=planner.propose_revision(before,'range-change',before['revision'],change,None,[requirement])
    study.store.apply_revision_command('range-change',before['revision'],change,None,patch)
    after=study.store.snapshot()
    assert after['experiments']==before['experiments']
    assert after['executions']==before['executions'] and after['artifacts']==before['artifacts']
    q1=before['experiments']['Q1']['question_id'];q2=before['experiments']['Q2']['question_id']
    assert after['questions'][q1]['answer_status']=='needs_update'
    assert after['questions'][q2]==before['questions'][q2]
    assert after['spec']['requirement']==SOURCE
    assert after['spec']['requirements'][:2]==before['spec']['requirements']
