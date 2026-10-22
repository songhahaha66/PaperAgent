"""Replay answer/reference and scope failures with controlled replies, no live API.

Worker receipts are synthetic unit fixtures; Agent routing uses the real tool graph.
The parent session runs kernel/export integration separately.
"""
import asyncio
import json
from types import SimpleNamespace
from langchain_core.messages import ToolMessage
from ai_system.graph.context import GraphContext
from ai_system.research import operations
from ai_system.research.control import delivery_status,graph_view,refresh_registry
from ai_system.research.domain import current_results
from ai_system.research.feedback import ReportEdit,interpretation_version,revise_interpretation
from ai_system.research.loop import prepare_action,resume
from ai_system.research.models import WorkerResult
from ai_system.research.planner import decision_context
from ai_system.research.store import ResearchStore
from ai_system.research.views import experiment_context
from research_tools_helpers import add_experiment, continue_experiment, ResearchToolsModel, tool, _manual_worker_handoff


def commit_count(store,eid,value,scope,*,lineage=None):
    order=store.claim(eid);ref=store.allocate_ref('R')
    row={'id':ref,'subproblem':eid,'kind':'number','name':'record_count','value':str(value),
         'unit':'条','run_id':'run_fixture_'+ref,'meaning':{'object':'源记录','scope':scope,
             'statistic':'符合所述范围的行数','conditions':'保留原始行关系','lineage':lineage or []}}
    store.commit(WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',
        results=[row],report={'status':'completed','summary':'该范围内的记录数 {{R:'+ref+'}}',
                             'key_findings':[],'limitations':['仅限所述范围，未验证其它过滤规则。']}))
    return ref


def study(tmp_path):
    (tmp_path/'data').mkdir();(tmp_path/'data/rows.csv').write_text('row_id,category\n1,A\n2,A\n3,B\n')
    store=ResearchStore(tmp_path,'replay');store.initialize('分析总体和指定类别子集，说明统计范围。')
    eid=add_experiment(store,'总体和类别子集分别有哪些证据？',method='按原始行统计总量及类别子集',
        conditions='所给源文件，保留原始行，允许总量及类别子计数',evaluation='核对范围与实际输出')
    ref=commit_count(store,eid,3,'全部源记录，不按类别过滤')
    return store,eid,ref,store.snapshot()['experiments'][eid]['question_id']


def test_missing_refs_are_saved_as_incomplete_then_fixed_in_the_same_agent(tmp_path):
    store,eid,ref,qid=study(tmp_path);executions=store.snapshot()['executions']
    def respond(messages):
        rows=[m for m in messages if isinstance(m,ToolMessage)]
        if not rows:return tool('research_update_answer',{'question_id':qid,'statement':'已回答总体问题',
            'coverage':'answered','stop_reason':'现有实验足够'},'missing')
        if len(rows)==1:
            saved=json.loads(rows[-1].content)
            assert saved['answer_status']=='needs_update' and saved['answer_gaps']
            status=delivery_status(store.snapshot());assert not status['ready'] and status['blockers']
            return tool('research_deliver',{'reason':'改写交付理由不能补依据'},'premature')
        if len(rows)==2:
            assert rows[-1].status=='error' and '完整回答缺少实际依据' in rows[-1].content
            return tool('research_read_question',{'question_id':qid},'read')
        if len(rows)==3:
            result=json.loads(rows[-1].content)['experiments'][0]['results'][0]
            assert result['id']==ref and result['meaning']['scope']=='全部源记录，不按类别过滤'
            assert result['input_refs']['data/rows.csv']
            return tool('research_update_answer',{'question_id':qid,'statement':'总体计数 {{R:'+ref+'}}，子集仍未回答',
                'artifact_refs':[ref],'coverage':'partial','stop_reason':'本轮只验证总体范围，子集证据仍未知'},'fix')
        assert json.loads(rows[-1].content)['answer_gaps']==[]
        return tool('research_deliver',{'title':'已有结果与未知','reason':'已明确部分答案及边界'},'deliver')
    model=ResearchToolsModel(respond=respond)
    ctx=GraphContext.create(str(tmp_path),planner_llm=model,max_solver_steps=6);ctx.research=store
    assert not asyncio.run(prepare_action(ctx))
    assert model.calls==5 and store.snapshot()['publication']['stage']=='write'
    assert store.snapshot()['executions']==executions
    resume(store)
    assert store.snapshot()['action']['capability']=='writing.write'


def test_distinct_scopes_are_retained_and_reused_with_sources_in_writing(tmp_path):
    from ai_system.modules.writing.sections import section_request
    store,first,r1,qid=study(tmp_path)
    result=operations.schedule_experiment(SimpleNamespace(research=store),operations.ExperimentInput(
        question_id=qid,artifact_refs=[r1],plan={'conditions':'只取 category=A，保持原始行身份',
            'evaluation':'分别解释总体与子集，不能替代','candidates':[{'name':'类别统计','instruction':'复用总体的源记录与版本，读取既有资料统计子集'}]}).model_dump())
    _manual_worker_handoff(store);second=result['experiment_id']
    r2=commit_count(store,second,2,'仅 category=A 的源记录')
    before=store.snapshot();records=experiment_context(before)
    assert before['experiments'][second]['dependencies']==[first]
    values={r['id']:r for e in records for r in e['results']}
    assert set(values)=={r1,r2} and values[r1]['meaning']['scope']!=values[r2]['meaning']['scope']
    assert values[r2]['input_refs']['task:'+first]==before['experiments'][first]['execution_id']
    ctx=GraphContext.create(str(tmp_path),planner_llm=None);ctx.research=store;refresh_registry(ctx)
    prompt=section_request('solve:'+first,'范围与结果',graph_view(before),ctx)
    assert all(v['reference'] in prompt and v['meaning']['scope'] in prompt and v['content_hash'] in prompt for v in values.values())
    assert 'data/rows.csv' in prompt and store.snapshot()==before
    with store._transaction() as db:
        state=store._read(db);state.setdefault('activity',[]).append({'id':'old','capability':'run_python','status':'completed',
            'experiment_id':second,'question_id':qid,'inputs':{'code':'PRIVATE_LONG_CODE'*2000},'output':{'report':'DUPLICATE_REPORT'*2000}})
        store._save(db,state,'activity_fixture',{})
    _,context=decision_context(store,store.snapshot())
    assert 'artifacts' not in context and 'PRIVATE_LONG_CODE' not in json.dumps(context) and 'DUPLICATE_REPORT' not in json.dumps(context)
    assert {r['id'] for e in context['experiments'] for r in e['results']}=={r1,r2}


def test_continuation_preserves_same_name_results_with_different_declared_scope(tmp_path):
    store,eid,r1,_=study(tmp_path)
    continue_experiment(store,eid,'在同一原始行统计方法内补类别子计数')
    r2=commit_count(store,eid,2,'仅 category=A 的源记录')
    assert {r['id'] for r in current_results(store.snapshot(),{eid})}=={r1,r2}
    before=store.snapshot();version=interpretation_version(before,before['experiments'][eid])
    revise_interpretation(store,eid,version,ReportEdit(summary='总体与子集范围不同，不能相互替代。',key_findings=[],limitations=['未验证其它筛选条件']))
    after=store.snapshot()
    assert after['executions']==before['executions']
    assert {r['id'] for r in current_results(after,{eid})}=={r1,r2}


def test_same_scope_new_lineage_updates_current_version_and_keeps_old_provenance(tmp_path):
    store,eid,r1,_=study(tmp_path)
    before=store.snapshot();old=before['artifacts'][r1]
    continue_experiment(store,eid,'核对相同统计范围并保留明确的原始行键')
    lineage=[{'path':'data/rows.csv','source_path':'data/rows.csv','row_keys':['row_id'],'transformation':'显式保留原始行键'}]
    r2=commit_count(store,eid,3,'全部源记录，不按类别过滤',lineage=lineage)
    after=store.snapshot()
    assert {r['id'] for r in current_results(after,{eid})}=={r2}
    assert after['artifacts'][r1]==old and after['artifacts'][r2]['payload']['meaning']['lineage']==lineage
    assert after['executions'][old['producer_attempt']]['result']==before['executions'][old['producer_attempt']]['result']
