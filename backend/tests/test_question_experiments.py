"""Canonical ownership, migration and graph recovery without external model calls."""
from services.file_services.workspace_layout import workspace_path
import asyncio
import json
import shutil
from types import SimpleNamespace

from ai_system.research.store import ResearchStore
from ai_system.research.models import WorkerResult, IssueReport, ResearchRevision
from ai_system.research import operations
from research_tools_helpers import add_experiment,answer
from ai_system.research.loop import prepare_action, resume
from ai_system.research.control import graph_view
from ai_system.research.domain import experiment_report
from services.chat_services.research_session import research_snapshot
from services.file_services.workspace_fs import WorkspaceFS


def experiment(store, *, question_id=None, name='窗口均值'):
    return add_experiment(store,'怎样预测下一周需求？',question_id=question_id,title=name,
        method=name,conditions='前15天训练，后5天固定验证',evaluation='比较MAE，差异不足时保留两种方法')


def finish(store, eid, *, failed=False):
    order=store.claim(eid)
    workspace=store.root/'experiments'/order.attempt_id/'workspace'
    fs=WorkspaceFS(str(workspace));run=fs.create_run()
    fs.save_run_input(run,'print(42)')
    fs.save_run_stdout(run,'42\n')
    shutil.copytree(workspace_path(workspace, 'runs'),workspace_path(store.root, 'runs'),dirs_exist_ok=True)
    rootfs=WorkspaceFS(str(store.root));rootfs._manifest['runs'].update(fs._manifest['runs']);rootfs._save_manifest()
    ref=store.allocate_ref('R')
    report={'status':'failed' if failed else 'completed','summary':'输出为 {{R:'+ref+'}}','approach':'实测方法'}
    result=WorkerResult(order=order,execution_status='failed' if failed else 'succeeded',
        acceptance='inconclusive' if failed else 'accepted',report=report,
        results=[] if failed else [{'id':ref,'subproblem':eid,'kind':'number','name':'误差','value':'42','run_id':run}],
        issues=[IssueReport(task_id=eid,category='execution',message='执行环境故障')] if failed else [])
    return result,store.commit(result)


def test_multiple_methods_keep_ownership_and_failed_method_does_not_block_question(tmp_path):
    store=ResearchStore(tmp_path,'research');store.initialize('预测下一周需求并比较方法。')
    first=experiment(store);qid=store.snapshot()['experiments'][first]['question_id']
    second=experiment(store,question_id=qid,name='线性趋势')
    bad=experiment(store,question_id=qid,name='仿真候选')
    result,receipt=finish(store,first);finish(store,second);finish(store,bad,failed=True)
    assert store.commit(result)==receipt
    state=store.snapshot()
    assert result.reviews==[] and state['experiments'][first]['acceptance']=='accepted'
    assert len(state['questions'])==1 and len(state['experiments'])==3
    assert not {'tasks','attempts','evidence','workflow'}&state.keys()
    assert all('report' not in e and 'design' not in e for e in state['experiments'].values())
    assert {a['experiment_id'] for a in state['artifacts'].values()}=={first,second,bad}
    assert {'result','code','stdout','report'}<={a['type'] for a in state['artifacts'].values()}
    assert not state['questions'][qid].get('judgment')
    assert store.delivery_ready(state), 'answer metadata does not gate file export'
    refs=[r['id'] for r in store.registry_payload()['results']]
    answer(store,question_id=qid,statement='两项方法已得到固定验证输出',artifact_refs=refs,
        coverage='answered',stop_reason='已比较给定方法，仿真执行故障另行保留')
    state=store.snapshot();assert store.delivery_ready(state)
    assert len(store.registry_payload()['results'])==2
    store.recover();resume(store)
    assert store.snapshot()['executions']==state['executions']
    thread=workspace_path(tmp_path, '.system/graph_thread.json')
    thread.write_text(json.dumps({'engine_version':'research-v3','thread_id':'research'}))
    view=research_snapshot(tmp_path,'work','paused').model_dump()
    assert 'tasks' not in view and 'plans' not in view and 'outcomes' not in view
    assert len(view['experiments'])==3 and view['questions'][0]['experiment_ids']==[first,second,bad]
    assert len(graph_view(state)['analysis']['questions'])==1


def test_one_dispatcher_recovers_execution_receipt_and_uses_flat_action(tmp_path,monkeypatch):
    store=ResearchStore(tmp_path,'research');store.initialize('预测下一周需求。')
    calls=[]
    async def stage(*args):pass
    ctx=SimpleNamespace(research=store,planner_llm=None,
                        events=SimpleNamespace(stage=stage,emit=stage))
    async def decide(self,ctx,context,observation_id):
        calls.append(context)
        qid=operations.update_question(ctx,{'question':'怎样预测下一周需求？','question_id':None,'base_version':1,
            'title':'','reason':'','source_question_ids':[],'experiment_ids':[],'retire_source_ids':[]})['question_id']
        operations.schedule_experiment(ctx,operations.ExperimentInput(question_id=qid,title='窗口均值',
            plan={'conditions':'最后五天固定验证','evaluation':'MAE',
                  'candidates':[{'name':'窗口均值','instruction':'最后三天均值'},
                                {'name':'线性趋势','instruction':'线性拟合'}],
                  'csv':{'dataset':'data/source.csv','target':'demand','features':['day']}}).model_dump())
    monkeypatch.setattr('ai_system.research.planner.Planner.run',decide)
    assert not asyncio.run(prepare_action(ctx))
    action=store.snapshot()['action'];eid=action['target'];finish(store,eid)
    with store._transaction() as db:
        state=store._read(db);state['action']['status']='running'
        store._save(db,state,'interrupted_after_commit',{})
    store.recover();resume(store)
    async def pause(self,ctx,context,observation_id):
        operations.pause(ctx,{'message':'本轮检查结束'})
    monkeypatch.setattr('ai_system.research.planner.Planner.run',pause)
    assert asyncio.run(prepare_action(ctx))
    assert len(store.snapshot()['executions'])==1 and len(calls)==1
    original=store.snapshot();revision=ResearchRevision(command_id='change',base_revision=original['revision'],
        change='改为预测两周',experiment_ids=[eid],requirements=[{
            'id':'RQ1','statement':'预测两周','source_quote':'预测两周','kind':'goal','task_ids':[eid],'checks':[]}])
    receipt=store.apply_revision_command('change',original['revision'],'改为预测两周',[eid],revision)
    assert store.apply_revision_command('change',original['revision'],'改为预测两周',[eid],revision)==receipt
    current=store.snapshot()
    assert current['experiments'][eid]['validity']=='current'
    assert current['questions'][current['experiments'][eid]['question_id']]['answer_status'] in {'needs_update','unanswered'}
    assert experiment_report(current,current['experiments'][eid])==experiment_report(original,original['experiments'][eid])
    assert current['artifacts']==original['artifacts'] and store.registry_payload()['results']
