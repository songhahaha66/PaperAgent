"""Canonical ownership, migration and graph recovery without external model calls."""
import asyncio
import json
import shutil
from types import SimpleNamespace

from ai_system.research.store import ResearchStore
from ai_system.research.models import WorkerResult, IssueReport, ResearchRevision
from ai_system.research import operations
from research_tools_helpers import add_experiment,answer
from ai_system.research.loop import prepare_action, act, resume
from ai_system.research.control import graph_view
from ai_system.research.domain import experiment_report
from services.chat_services.research_session import research_snapshot
from services.file_services.workspace_fs import WorkspaceFS


def experiment(store, *, question_id=None, name='窗口均值'):
    return add_experiment(store,'怎样预测下一周需求？',question_id=question_id,title=name,
        method=name,conditions='前15天训练，后5天固定验证',evaluation='比较MAE，差异不足时保留两种方法')


def finish(store, eid, *, failed=False):
    order=store.claim(eid)
    workspace=store.root/'.system/attempts'/order.attempt_id/'workspace'
    fs=WorkspaceFS(str(workspace));run=fs.create_run()
    fs.save_run_input(run,'print(42)')
    fs.save_run_stdout(run,'42\n')
    shutil.copytree(workspace/'runs',store.root/'runs',dirs_exist_ok=True)
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
    assert not store.delivery_ready(state)  # Results alone are not a recorded answer.
    refs=[r['id'] for r in store.registry_payload()['results']]
    answer(store,question_id=qid,statement='两项方法已得到固定验证输出',artifact_refs=refs,
        coverage='answered',stop_reason='已比较给定方法，仿真执行故障另行保留')
    state=store.snapshot();assert store.delivery_ready(state)
    assert len(store.registry_payload()['results'])==2
    store.recover();resume(store)
    assert store.snapshot()['executions']==state['executions']
    thread=tmp_path/'.system/graph_thread.json'
    thread.write_text(json.dumps({'engine_version':'research-v3','thread_id':'research'}))
    view=research_snapshot(tmp_path,'work','paused').model_dump()
    assert 'tasks' not in view and 'plans' not in view and 'outcomes' not in view
    assert len(view['experiments'])==3 and view['questions'][0]['experiment_ids']==[first,second,bad]
    assert len(graph_view(state)['analysis']['questions'])==1


def test_schema_three_migration_retains_results_and_is_idempotent(tmp_path):
    store=ResearchStore(tmp_path,'research');store.initialize('预测下一周需求。')
    eid=experiment(store);finish(store,eid)
    state=store.snapshot();report=experiment_report(state,state['experiments'][eid])
    old=json.loads(json.dumps(state));old['schema_version']=3
    old['tasks']=old.pop('experiments');old['attempts']=old.pop('executions')
    old['evidence']={rid:a for rid,a in old.pop('artifacts').items() if a['type']=='result'}
    old.pop('questions');old.pop('action');old.pop('publication')
    item=old['tasks'][eid];item['active_attempt']=item.pop('execution_id');item.pop('question_id');item.pop('report_ref')
    item.update(report=report,design=None)
    raw=json.dumps(old,ensure_ascii=False)
    with store._transaction() as db:
        db.execute('UPDATE research SET payload=? WHERE id=?',(raw,'research'))
        db.execute('DELETE FROM domain_migrations WHERE version=7')
    upgraded=ResearchStore(tmp_path,'research');before=upgraded.snapshot()
    assert before['schema_version']==5
    assert set(before['artifacts'])>=set(old['evidence'])
    assert experiment_report(before,before['experiments'][eid])==report
    assert ResearchStore(tmp_path,'research').snapshot()==before
    with upgraded._connect() as db:
        assert db.execute('SELECT raw FROM legacy_records WHERE source=?',('research/research/v3',)).fetchone()[0].decode()==raw


def test_one_dispatcher_recovers_execution_receipt_and_uses_flat_action(tmp_path,monkeypatch):
    store=ResearchStore(tmp_path,'research');store.initialize('预测下一周需求。')
    calls=[]
    async def stage(*args):pass
    ctx=SimpleNamespace(research=store,planner_llm=None,max_planning_calls=None,
                        events=SimpleNamespace(stage=stage,emit=stage))
    async def decide(self,ctx,context,observation_id):
        calls.append(context)
        qid=operations.update_question(ctx,{'question':'怎样预测下一周需求？','question_id':None,'base_version':1,
            'title':'','reason':'','source_question_ids':[],'experiment_ids':[],'retire_source_ids':[]})['question_id']
        operations.schedule_experiment(ctx,operations.ExperimentInput(question_id=qid,title='窗口均值',
            plan={'conditions':'最后五天固定验证','evaluation':'MAE','candidates':[{'name':'窗口均值','instruction':'最后三天均值'}]}).model_dump())
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


def test_existing_graph_runs_real_kernel_then_writes_and_publishes_question(tmp_path,monkeypatch):
    from dataclasses import asdict
    from langchain_core.messages import AIMessage
    from ai_system.graph.build import compile_graph
    from ai_system.graph.context import GraphContext
    from ai_system.modules.experiments.planning import experiment_design
    class Writer:
        async def ainvoke(self,messages):
            return AIMessage(content='本节依据实测结果 {{R:R1}} 说明当前条件下的结论，推广仍需验证。')
    async def decide(self,ctx,context,observation_id):
        state=ctx.research.snapshot()
        if not state['experiments']:
            from research_tools_helpers import add_question
            qid=add_question(ctx.research,'给定计算的结果是多少？')
            return operations.schedule_experiment(ctx,operations.ExperimentInput(question_id=qid,title='固定计算',
                plan={'conditions':'Python整数运算','evaluation':'核对精确值','candidates':[{'name':'固定计算','instruction':'计算 6*7 并登记'}]}).model_dump())
        eid=next(iter(state['experiments']));qid=state['experiments'][eid]['question_id']
        refs=[r['payload']['id'] for r in state['artifacts'].values() if r['type']=='result']
        answer(ctx.research,question_id=qid,statement='整数计算结果为42',artifact_refs=refs,
            coverage='answered',stop_reason='已完成指定整数运算')
        return operations.deliver(ctx,{'title':'计算研究','reason':'已有真实计算结果，开始整理草稿'})
    async def solve(branch,runtime):
        ctx=runtime.context;eid=branch['subproblem']['id']
        run=await ctx.kernels.get(eid).execute("print(6*7)")
        assert run.ok
        result=ctx.registry.record_value(subproblem=eid,name='计算结果',value='42',run_id=run.run_id)
        return {'solutions':{eid:{'status':'completed','summary':'结果 {{R:'+result.id+'}}','approach':'整数计算'}},
                'designs':{eid:experiment_design(branch['experiment'])}}
    monkeypatch.setattr('ai_system.research.planner.Planner.run',decide)
    monkeypatch.setattr('ai_system.modules.experiments.execution.solve',solve)
    async def run():
        ctx=GraphContext.create(str(tmp_path),planner_llm=Writer(),max_review_rounds=0,check_claims=False)
        ctx.research=ResearchStore(tmp_path,'graph')
        try:
            result=await compile_graph().ainvoke({'task':'计算6*7并写出结果。'},{'recursion_limit':40},context=ctx)
            assert not result['research_blocked']
            state=ctx.research.snapshot()
            assert state['outcome']=='completed' and state['publication']['stage']=='completed'
            assert len(state['executions'])==1 and (tmp_path/'paper.md').is_file()
            assert '42' in (tmp_path/'paper.md').read_text()
            assert {a['type'] for a in state['artifacts'].values()}>={'result','code','stdout','report'}
        finally:await ctx.kernels.close()
    asyncio.run(run())
