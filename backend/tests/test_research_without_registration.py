"""Live Agent flow and migration with files/runs, without answer registration."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from ai_system.modules.registry import capabilities, invoke
from ai_system.research import operations
from ai_system.research.loop import prepare_action, _act
from ai_system.research.planner import working_context
from ai_system.research.store import ResearchStore
from ai_system.research.views import questions
from helpers.context import session_context
from research_tools_helpers import ResearchToolsModel, tool
from services.chat_services.research_session import research_snapshot, answer_research_question
from services.file_services.workspace_layout import workspace_path


REMOVED={'research.update_answer','research.schedule_experiment','research.continue_experiment',
         'experiments.compare','experiments.execute'}
ANSWER_FIELDS={'judgment','judgments','coverage','stop_reason','answer_status','answer_gaps',
               'artifact_refs','basis_versions','interpretation_versions','limitations','next_step'}


@pytest.mark.usefixtures('offline_kernel')
def test_model_comparison_skill_runs_in_main_agent_and_delivers_actual_files(tmp_path):
    folder=tmp_path/'data';folder.mkdir()
    (folder/'observations.csv').write_text('x,y\n'+''.join(f'{i},{2*i+1}\n' for i in range(20)))
    code="""import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.linear_model import LinearRegression
from sklearn.dummy import DummyRegressor
data=pd.read_csv('data/observations.csv')
train=data.iloc[:15]; test=data.iloc[15:]
predictions=test.copy()
for name,model in [('baseline',DummyRegressor()),('linear',LinearRegression())]:
    model.fit(train[['x']],train['y'])
    predictions[name]=model.predict(test[['x']])
    print(name, 'RMSE', float(np.sqrt(np.mean((predictions[name]-test.y)**2))))
Path('outputs/intermediate').mkdir(parents=True,exist_ok=True)
predictions.to_csv('outputs/intermediate/predictions.csv',index=False)
"""
    def respond(messages):
        rows=[m for m in messages if isinstance(m,ToolMessage)]
        if not rows:return tool('research_list_skills',{},'list')
        if len(rows)==1:
            assert 'model_comparison' in rows[-1].content
            return tool('research_read_skill',{'key':'model_comparison'},'skill')
        if len(rows)==2:
            assert '同一份划分' in rows[-1].content
            return tool('research_run_python',{'code':code,'title':'比较基线和线性回归'},'compute')
        if len(rows)==3:
            result=json.loads(rows[-1].content)
            assert result['execution_status']=='succeeded' and 'linear RMSE' in result['observation']
            return tool('writing_edit',{'path':'paper.md','content':'# 方法比较\n训练前15行，测试后5行；实测预测见 outputs/intermediate/predictions.csv。\n仅用于该数据与划分，未验证外推。'},'write')
        return tool('research_deliver',{'title':'实际预测与局限'},'deliver')
    assert not {c['name'] for c in capabilities().describe()} & REMOVED
    ctx=session_context(tmp_path,ResearchToolsModel(respond=respond))
    async def scenario():
        try:
            assert not await prepare_action(ctx)
            action=ctx.research.snapshot()['action']
            assert action['capability']=='writing.export'
            await _act({'action_id':action['id']},SimpleNamespace(context=ctx))
        finally:await ctx.kernels.close()
    asyncio.run(scenario())
    state=ctx.research.snapshot()
    assert state['outcome']=='completed' and state['exports']==['paper.md']
    assert all(not state[key] for key in ('questions','experiments','executions','artifacts','claims'))
    assert len(ctx.fs._manifest['runs'])==1
    assert (tmp_path/'outputs/intermediate/predictions.csv').read_text().startswith('x,y,baseline,linear')
    assert not REMOVED.intersection(a['capability'] for a in state['activity'])


def test_skill_reads_obey_user_selection_and_never_create_an_experiment(tmp_path):
    ctx=session_context(tmp_path)
    ctx.research.set_skill_preferences({'mode':'selected','selected':['regression'],'disabled':[]},0)
    async def scenario():
        rows=await invoke(ctx,'research.list_skills')
        assert [s['key'] for s in rows['skills']]==['regression']
        with pytest.raises(ValueError,match='未在当前项目启用'):
            await invoke(ctx,'research.read_skill',key='model_comparison')
        with pytest.raises(ValueError,match='技能不存在'):
            await invoke(ctx,'research.read_skill',key='unknown')
    asyncio.run(scenario())
    assert not ctx.research.snapshot()['experiments']


@pytest.mark.parametrize('capability',['research.update_answer','experiments.execute'])
def test_migration_archives_answers_and_retires_pending_dispatch_without_touching_runs(tmp_path,capability):
    ctx=session_context(tmp_path);store=ctx.research
    qid=operations.update_question(ctx,{'question':'原始研究问题','title':'原问题'})['question_id']
    run_id=ctx.fs.create_run()
    ctx.fs.save_run_input(run_id,'print(6)');ctx.fs.save_run_stdout(run_id,'6\n');ctx.fs.finish_run(run_id)
    with store._transaction() as db:
        state=store._read(db)
        old={'judgment':'原始答案','coverage':'partial','stop_reason':'未完成部分',
             'judgments':[{'statement':'旧答案'}],'limitations':['有限范围'],
             'artifact_refs':['R1'],'answer_status':'needs_update','basis_versions':{'R1':'hash'}}
        state['questions'][qid].update(deepcopy(old))
        state['questions'][qid]['history']=[deepcopy(state['questions'][qid])]
        original=deepcopy(state['questions'][qid])
        state['action']={'id':'old-dispatch','capability':capability,'status':'running','target':None}
        store._save(db,state,'legacy_fixture',{})
        db.execute('DELETE FROM domain_migrations WHERE version=12')
    original_manifest=workspace_path(tmp_path,'manifest.json').read_bytes()
    migrated=ResearchStore(tmp_path,store.research_id).snapshot()
    assert migrated['action'] is None
    assert not ANSWER_FIELDS.intersection(migrated['questions'][qid])
    assert not ANSWER_FIELDS.intersection(migrated['questions'][qid]['history'][0])
    archive=next(row for row in migrated['legacy_records'] if row['source']=='retired-question-answer')
    assert archive['raw']==original
    assert workspace_path(tmp_path,'manifest.json').read_bytes()==original_manifest
    assert not ANSWER_FIELDS.intersection(questions(migrated)[0])
    assert not ANSWER_FIELDS.intersection(working_context(ctx)['questions'][0])
    assert ResearchStore(tmp_path,store.research_id).snapshot()==migrated


@pytest.mark.usefixtures('offline_kernel')
def test_read_only_followup_receives_direct_output_without_registered_results(tmp_path,monkeypatch):
    ctx=session_context(tmp_path)
    captured=[]
    class Model:
        async def astream(self,messages):
            captured.append(messages[-1].content)
            if False:yield None
    class Stream:
        async def send_json_block(self,*args):pass
    async def scenario():
        try:
            q=await invoke(ctx,'research.create_question',question='实际总和')
            run=await invoke(ctx,'research.run_python',question_id=q['question_id'],code="print('actual_total',6)")
            workspace_path(tmp_path,'.system/graph_thread.json').write_text(json.dumps({
                'thread_id':ctx.research.research_id,'engine_version':'research-v3'}))
            snapshot=research_snapshot(tmp_path,'work','paused')
            assert not snapshot.results and len(snapshot.runs)==1
            await answer_research_question(Model(),Stream(),snapshot,tmp_path,'刚才算出什么？',[],q['question_id'])
            assert run['run_id'] in captured[0] and 'actual_total 6' in captured[0]
        finally:await ctx.kernels.close()
    asyncio.run(scenario())
