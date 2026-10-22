"""Real diabetes data, sklearn kernels and SQLite; model replies are deterministic."""

from ai_system.research.domain import findings as research_findings
import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sklearn.datasets import load_diabetes
from sklearn.metrics import mean_squared_error
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.graph.context import GraphContext
from ai_system.modules.experiments import supervised as engine
async def observe_comparison(ctx,branch):
    # Historical handoff tests consume a view of the existing CSV record.
    record=await engine.compare(ctx,branch)
    index=record.get('chosen_index')
    row=next((r for r in record['branches'] if r['index']==index),None)
    winner=SimpleNamespace(candidate=branch['design']['candidates'][index],candidate_index=index) if row else None
    return SimpleNamespace(record=record,chosen_index=index,winner=winner,to_record=lambda:record)
from ai_system.modules.experiments.store import ExperimentStore,CandidateCode,SupervisedSpec
from ai_system.research.store import ResearchStore
from ai_system.research.control import worker_context

TASK='根据 data/diabetes.csv 的结构化数据比较两种回归方案，预测 target；独立留出验证，不用模型自评决定优胜。'
RIDGE='''from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
def make_estimator(seed):
    print("FAKE_SCORE RMSE=0 AUC=1")
    return make_pipeline(StandardScaler(),Ridge(alpha=1))
'''
TREE='''from sklearn.pipeline import make_pipeline
from sklearn.ensemble import RandomForestRegressor
def make_estimator(seed):
    return make_pipeline(RandomForestRegressor(n_estimators=8,max_depth=1,random_state=seed))
'''
BAD_A="def make_estimator(seed):\n    raise ValueError('failed A')\n"
BAD_B="def make_estimator(seed):\n    raise RuntimeError('failed B')\n"
DUMMY_A="from sklearn.pipeline import make_pipeline\nfrom sklearn.dummy import DummyRegressor\ndef make_estimator(seed):\n    return make_pipeline(DummyRegressor(strategy='mean'))\n"
DUMMY_B="from sklearn.pipeline import make_pipeline\nfrom sklearn.preprocessing import StandardScaler\nfrom sklearn.dummy import DummyRegressor\ndef make_estimator(seed):\n    return make_pipeline(StandardScaler(),DummyRegressor(strategy='mean'))\n"


def candidate(name,method,idea):return {'name':name,'method_keys':[method],'idea':idea,'formulation':'supervised regression','steps':['fit training rows','predict validation rows'],'validation':'project RMSE','assumptions':[]}


@pytest.fixture
def setup(tmp_path,monkeypatch):
    root=tmp_path/'study';(root/'data').mkdir(parents=True)
    data=load_diabetes(as_frame=True).frame;data.insert(0,'sample_id',[f'patient-{i}' for i in range(len(data))]);data.to_csv(root/'data/diabetes.csv',index=False)
    store=ResearchStore(root,'study');analysis={'overall_analysis':'监督学习比较','subproblems':[{'id':'Q1','title':'回归预测','question':TASK,'depends_on':[],'deliverables':['独立验证']}],
        'requirements':[{'id':'RQ1','statement':'回归预测','source_quote':'预测 target','kind':'goal','task_ids':['Q1'],'checks':[]}],'assumptions':[]}
    store.initialize(TASK,analysis)
    cfg=ExperimentStore(store);spec=SupervisedSpec(dataset='data/diabetes.csv',target='target',features=list(load_diabetes().feature_names),id_column='sample_id',negative_control_tolerance=.15).model_dump();cfg.set_policy(True,spec)
    parent=GraphContext.create(str(root),planner_llm=None);parent.research=store
    order=store.claim('Q1');ctx=worker_context(parent,order)
    candidates=[candidate('shallow','gbdt','shallow random forest'),candidate('ridge','regression','scaled ridge')]
    branch={'source':TASK,'analysis':analysis,'subproblem':analysis['subproblems'][0],'data_profile':'442 observations, clinical measurements',
        'research_contract':{'checks':[]},'design':{'candidates':candidates,'scores':[{'total':15},{'total':1}],'chosen_index':0,'chosen':candidates[0]}}
    calls=[];codes={'shallow':TREE,'ridge':RIDGE}
    async def ask(_ctx,schema,system,prompt,timeout,tokens=2000):
        assert schema is CandidateCode
        content=json.loads(prompt)
        assert set(content)=={'candidate','task_type','metric','features','n_train','seed','previous_attempt'}
        assert 'X_validation' not in content and 'y_validation' not in content
        assert set(content['candidate'])=={'name','method_keys','idea','formulation','steps','variant','estimator_contract'}
        chosen=next(c for c in candidates if c['name']==content['candidate']['name'])
        assert all(content['candidate'][key]==chosen[key] for key in ('idea','formulation','steps'))
        record=cfg.read(_ctx.experience_order.task_id,_ctx.experience_order.revision)
        row=next(r for r in record['branches'] if r['index']==content['candidate']['variant'])
        history=row.get('history',[])
        if history:
            assert content['previous_attempt']==history[-1]
            assert history[-1]['status'] in engine.RECOVERABLE
            assert history[-1]['name']==chosen['name']
            assert history[-1]['index']==row['index']
        else:
            assert content['previous_attempt'] is None
        calls.append(content['candidate']['name']);return CandidateCode(code=codes[content['candidate']['name']])
    monkeypatch.setattr(engine,'ask',ask)
    return SimpleNamespace(root=root,store=store,cfg=cfg,spec=spec,parent=parent,ctx=ctx,order=order,branch=branch,calls=calls,codes=codes)


def run(setup):
    async def work():
        try:return await observe_comparison(setup.ctx,setup.branch)
        finally:await setup.ctx.kernels.close()
    return asyncio.run(work())


def test_low_critic_better_measured_pipeline_wins_and_printed_score_is_ignored(setup):
    setup.codes['shallow']=TREE.replace('def make_estimator(seed):', 'def make_estimator(seed):\n    print("FAKE_RMSE 0")')
    setup.codes['ridge']=RIDGE.replace('RMSE=0 AUC=1','RMSE=999999 AUC=0')
    result=run(setup);record=result.to_record()
    assert record['status']=='winner' and result.chosen_index==1
    assert len(record['branches'])==2 and all(r['run_ids'] for r in record['branches'])
    ridge=record['branches'][1];assert 40<ridge['metric']<70 and ridge['metric']!=0
    measured=mean_squared_error([p['observed'] for p in ridge['predictions']],[p['prediction'] for p in ridge['predictions']])**.5
    assert ridge['metric']==pytest.approx(measured)
    assert record['protocol']['train_ids'] and record['protocol']['validation_ids']
    assert not set(record['protocol']['train_ids'])&set(record['protocol']['validation_ids'])
    assert ridge['baseline']==record['branches'][0]['baseline']==record['protocol']['baseline_value']
    assert all(r['code_hash'] and r['ast_hash'] and r['selection_reason'] for r in record['branches'])
    payload=json.loads(next((setup.ctx.workspace/'outputs/intermediate/comparisons').rglob('inputs-0.json')).read_text())
    assert 'y_validation' not in payload and len(payload['y_train'])==len(record['protocol']['train_ids'])
    budget=setup.cfg.budget('Q1')['used'];assert budget=={'model_calls':2,'executions':2,'candidates':2,'timeout_seconds':100}
    assert setup.calls==['shallow','ridge']


def test_completed_resume_never_repeats_calls_executions_or_resets_budget(setup):
    first=run(setup).to_record();before=copy.deepcopy(setup.cfg.budget('Q1'))
    setup.store.recover()
    from ai_system.research.operations import continue_experiment
    continue_experiment(SimpleNamespace(research=setup.store),{'experiment_id':'Q1','information_need':'读取并复用已完成的冻结比较结果'})
    order=setup.store.claim('Q1');ctx=worker_context(setup.parent,order);setup.ctx=ctx
    second=run(setup).to_record()
    assert second['chosen_index']==first['chosen_index'] and second['branches']==first['branches']
    assert setup.cfg.budget('Q1')==before and len(setup.calls)==2


def test_input_changed_before_promotion_is_not_promoted(setup,monkeypatch):
    original=setup.ctx.kernels.get
    def kernel(key):
        value=original(key);execute=value.execute
        async def changed(code):
            result=await execute(code)
            (setup.root/'data/diabetes.csv').write_text((setup.root/'data/diabetes.csv').read_text()+'\n')
            return result
        value.execute=changed;return value
    monkeypatch.setattr(setup.ctx.kernels,'get',kernel)
    result=run(setup)
    assert result.winner is None and result.record['status']=='input_changed'
    assert result.record['branches'][0]['run_ids']


def test_gateway_authorization_failure_stops_further_calls_and_is_not_a_method_error(setup,monkeypatch):
    class AuthenticationError(RuntimeError):pass
    calls=[]
    async def denied(*args,**kwargs):calls.append(1);raise AuthenticationError('do not persist provider secrets')
    monkeypatch.setattr(engine,'ask',denied)
    record=run(setup).to_record();assert record['status']=='no_valid' and record['provider_unavailable']
    assert calls==[1] and not any(r['run_ids'] for r in record['branches'])
    assert setup.cfg.budget('Q1')['used']['model_calls']==1
    setup.store.report_comparison_failure(record,setup.order);assert not research_findings(setup.store.snapshot())
    assert run(setup).record['status']=='no_valid' and calls==[1]
