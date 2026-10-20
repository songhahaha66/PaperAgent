"""Real diabetes data, sklearn kernels and SQLite; model replies are deterministic."""

from ai_system.research.domain import findings as research_findings
import asyncio
import copy
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sklearn.datasets import load_diabetes
from sklearn.metrics import mean_squared_error
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.graph.context import GraphContext
from ai_system.modules.experiments import supervised as engine
from ai_system.modules.experiments.exploration import explore
from ai_system.modules.experiments.store import ExperimentStore,CandidateCode,SupervisedSpec
from ai_system.research.store import ResearchStore,digest
from ai_system.research.models import ResearchConflict,PlanPatch,TaskChange,PlanProposal,WorkerResult
from ai_system.research.control import worker_context
from ai_system.research.planner import Planner,decision_context,validate_decision,EvidenceDecision,advance_research

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
    parent=GraphContext.create(str(root),planner_llm=None,explore_top_k=2,n_candidates=2);parent.research=store
    order=store.claim('Q1');ctx=worker_context(parent,order)
    candidates=[candidate('shallow','gbdt','shallow random forest'),candidate('ridge','regression','scaled ridge')]
    branch={'source':TASK,'analysis':analysis,'subproblem':analysis['subproblems'][0],'data_profile':'442 observations, clinical measurements',
        'research_contract':{'checks':[]},'design':{'candidates':candidates,'scores':[{'total':15},{'total':1}],'chosen_index':0,'chosen':candidates[0]}}
    calls=[];codes={'shallow':TREE,'ridge':RIDGE}
    async def ask(_ctx,schema,system,prompt,timeout,tokens=2000):
        assert schema is CandidateCode
        content=json.loads(prompt)
        assert set(content)<={'candidate','task_type','metric','features','n_train','seed'}
        assert 'X_validation' not in content and 'y_validation' not in content
        assert set(content['candidate'])=={'name','method_keys','idea','formulation','steps','variant','estimator_contract'}
        chosen=next(c for c in candidates if c['name']==content['candidate']['name'])
        assert all(content['candidate'][key]==chosen[key] for key in ('idea','formulation','steps'))
        calls.append(content['candidate']['name']);return CandidateCode(code=codes[content['candidate']['name']])
    monkeypatch.setattr(engine,'ask',ask)
    return SimpleNamespace(root=root,store=store,cfg=cfg,spec=spec,parent=parent,ctx=ctx,order=order,branch=branch,calls=calls,codes=codes)


def run(setup):
    async def work():
        try:return await explore(setup.ctx,setup.branch)
        finally:await setup.ctx.kernels.close()
    return asyncio.run(work())


def accept_formal_comparison(setup):
    """Real project measurements and handoff, then the normal store commit."""
    from ai_system.modules.experiments.handoff import handoff,proof_issues
    async def work():
        try:
            exploration=await explore(setup.ctx,setup.branch)
            assert exploration.winner is not None
            setup.branch['design'].update(chosen=exploration.winner.candidate,chosen_index=exploration.winner.candidate_index)
            await handoff(setup.ctx,setup.branch,exploration,[])
            proof=setup.ctx.comparison_proof
            report={'status':'completed','summary':'实际冻结指标已复核。',
                    'requirement_evidence':[{'requirement_id':'RQ1','evidence_refs':list(proof['protected'])}]}
            assert not proof_issues(setup.ctx,report)
            receipt=setup.store.commit(WorkerResult(order=setup.order,execution_status='succeeded',acceptance='accepted',
                results=list(proof['protected'].values()),report=report,design=setup.branch['design']))
            assert receipt['published'] and setup.store.snapshot()['tasks']['Q1']['acceptance']=='accepted'
            return setup.cfg.read('Q1',setup.order.revision)
        finally:
            await setup.ctx.kernels.close()
    return asyncio.run(work())


def comparison_writing_prompt(setup,sid='Q1'):
    from ai_system.research.control import refresh_registry
    from ai_system.modules.writing.sections import section_request
    refresh_registry(setup.parent)
    state={'task':TASK,'analysis':copy.deepcopy(setup.branch['analysis']),
           'designs':{'Q1':setup.branch['design']},'solutions':{}}
    if sid=='Q2':state['analysis']['subproblems'].append({'id':'Q2','title':'其他研究','question':'其他研究','depends_on':[]})
    return section_request('solve:'+sid,'回归预测',state,setup.parent)


def comparison_in_prompt(prompt):
    text=prompt.split('# 当前共同协议比较',1)[1]
    return json.JSONDecoder().raw_decode(text[text.index('[{'):])[0][0]


def test_accepted_comparison_reaches_planner_with_measured_candidates_and_boundaries(setup):
    record=accept_formal_comparison(setup)
    before=setup.store.snapshot();budget=setup.cfg.budget('Q1')
    _,context=decision_context(setup.store,before)
    entry=next(e for e in context['evidence'] if e.get('kind')=='comparison')
    summary=json.loads(entry['text'])
    assert entry['id']==record['id'] and entry['id'].startswith('P')
    assert summary['status']=='winner' and summary['chosen_index']==1
    assert [r['name'] for r in summary['branches']]==['shallow','ridge']
    assert [r['metric'] for r in summary['branches']]==[r['metric'] for r in record['branches']]
    assert summary['branches'][1]['metric']<summary['branches'][0]['metric']
    assert all(r['run_ids'] and r['code_hash'] and r['prediction_hash'] for r in summary['branches'])
    assert '"prior":' not in entry['text'] and 'FAKE_SCORE' not in entry['text'] and '"scores":' not in entry['text']
    protocol=summary['protocol']
    assert protocol['dataset']==setup.spec['dataset'] and protocol['features']==setup.spec['features']
    assert protocol['metric']=='RMSE' and protocol['higher_is_better'] is False
    assert protocol['split']=='random' and protocol['train_count']+protocol['validation_count']==442
    assert protocol['negative_control']=='permute_all_targets_before_split'
    assert protocol['tie_tolerance']==setup.spec['tie_tolerance']
    provenance=summary['provenance']
    assert provenance['table']=='experiment_comparisons' and provenance['record_id']==record['id']
    assert provenance['protocol_hash']==record['protocol_hash'] and summary['attempt_id']==setup.order.attempt_id
    assert set(provenance['formal_evidence_refs'])<=set(before['evidence'])
    assert {e['id'] for e in context['evidence'] if e['id'].startswith('R')}==set(before['evidence'])
    assert setup.store.snapshot()==before and setup.cfg.budget('Q1')==budget, 'Reading must not register synthetic results or consume comparison budget'


def test_writer_receives_both_measured_candidates_as_registered_renderable_references(setup):
    record=accept_formal_comparison(setup);before=setup.store.snapshot();budget=setup.cfg.budget('Q1');calls=list(setup.calls)
    prompt=comparison_writing_prompt(setup);summary=comparison_in_prompt(prompt)
    assert setup.branch['design']['chosen_index']==record['chosen_index']==1
    assert summary['id']==record['id'] and summary['status']=='winner' and summary['reason']==record['reason']
    assert [r['name'] for r in summary['branches']]==['shallow','ridge']
    assert summary['protocol']['data_hash']==record['protocol']['data_hash'] and summary['protocol']['metric']=='RMSE'
    assert summary['protocol']['negative_control']=='permute_all_targets_before_split'
    proof=record['formal_handoffs'][-1];rendered=[]
    assert proof['fresh_fit'] is False
    assert '.fit(' not in setup.ctx.fs.read_text(setup.ctx.fs.get_run_info(proof['run_id'])['input'])
    for row,measured in zip(summary['branches'],record['branches']):
        for field in ('metric','baseline'):
            match=re.fullmatch(r'\{\{R:(R\d+)\}\}',row[field]);assert match
            result=setup.parent.registry.get(match[1]);assert result and result.id in proof['evidence_refs']
            assert result.subproblem=='Q1' and result.run_id==proof['run_id']
            assert float(result.value)==pytest.approx(measured[field])
            setup.ctx.registry.verify(result.value,result.run_id)
            assert setup.parent.registry.render(row[field])==result.value
        rendered.append(float(setup.parent.registry.render(row['metric'])))
    assert rendered[1]<rendered[0] and rendered[1]!=0, 'Candidate stdout FAKE_SCORE must not become metric evidence'
    legacy=next(r for r in setup.parent.registry.results.values() if r.name=='comparison_rmse')
    assert float(legacy.value)==pytest.approx(record['branches'][1]['metric'])
    assert legacy.id in proof['evidence_refs'] and legacy.run_id==proof['run_id']
    serialized=json.dumps(summary,ensure_ascii=False)
    assert all(str(r['metric']) not in serialized for r in record['branches'])
    assert 'FAKE_SCORE' not in serialized and '"prior"' not in serialized
    assert '不证明统计显著' in prompt and 'only_valid' in prompt and 'tied' in prompt
    assert record['id'] not in comparison_writing_prompt(setup,'Q2')
    assert setup.cfg.budget('Q1')==budget and setup.calls==calls and setup.store.snapshot()==before


def test_stale_failed_and_unbound_comparisons_do_not_become_planner_evidence(setup,monkeypatch):
    record=accept_formal_comparison(setup);snapshot=setup.store.snapshot()
    damaged=[]
    for status in ('no_valid','budget_exhausted','input_changed','running'):
        altered=copy.deepcopy(record);altered['status']=status;damaged.append((status,altered))
    for label,field,value in [('candidates','candidate_hashes',['changed','changed']),('task','task_hash','changed'),
                              ('protocol','protocol_hash','changed'),('policy','policy_version',-1),
                              ('implementation','implementation_contract_version',-1),('no handoff','formal_handoffs',[])]:
        altered=copy.deepcopy(record);altered[field]=value;damaged.append((label,altered))
    altered=copy.deepcopy(record);altered['formal_handoffs'][0]['code_hash']='different-code';damaged.append(('formal code',altered))
    for label,altered in damaged:
        with monkeypatch.context() as patch:
            patch.setattr(ExperimentStore,'read',lambda *args,record=altered:record)
            _,context=decision_context(setup.store,snapshot)
            assert not any(e.get('kind')=='comparison' for e in context['evidence']),label
            assert '# 当前共同协议比较' not in comparison_writing_prompt(setup),label
    for label in ('stale task','different revision','different attempt','unpublished attempt','failed attempt',
                  'inactive proof','historical proof','different formal run','different formal metric'):
        altered=copy.deepcopy(snapshot);task=altered['tasks']['Q1']
        if label=='stale task':task['validity']='stale'
        elif label=='different revision':task['revision']+=1
        elif label=='different attempt':task['active_attempt']='not-the-accepted-attempt'
        elif label=='unpublished attempt':altered['attempts'][task['active_attempt']]['commit']['published']=False
        elif label=='failed attempt':altered['attempts'][task['active_attempt']]['execution_status']='failed'
        else:
            ref=record['formal_handoffs'][0]['evidence_refs'][0]
            if label=='inactive proof':altered['evidence'][ref]['active']=False
            elif label=='historical proof':altered['evidence'][ref]['producer_attempt']='historical-attempt'
            elif label=='different formal run':altered['evidence'][ref]['payload']['run_id']='another-run'
            else:altered['evidence'][ref]['payload']['value']='-999'
        _,context=decision_context(setup.store,altered)
        assert not any(e.get('kind')=='comparison' for e in context['evidence']),label
    data=setup.root/setup.spec['dataset'];original=data.read_bytes()
    try:
        data.write_bytes(original+b'\n')
        assert setup.cfg.accepted_summaries(snapshot)==[], 'Changed source files invalidate the comparison without replaying it'
        assert '# 当前共同协议比较' not in comparison_writing_prompt(setup)
    finally:data.write_bytes(original)
    assert setup.cfg.read('Q1',setup.order.revision)==record


@pytest.mark.parametrize('status',['winner','only_valid','tied'])
def test_comparison_selection_status_is_preserved_and_citable_without_new_runs(setup,status):
    if status=='only_valid':setup.codes['shallow']=BAD_A
    if status=='tied':
        setup.codes['shallow']=DUMMY_A
        setup.branch['design']['candidates'][1].update(name='mean alternative',idea='training mean')
        setup.codes['mean alternative']=DUMMY_B
    record=accept_formal_comparison(setup);assert record['status']==status
    snapshot=setup.store.snapshot();budget=setup.cfg.budget('Q1');calls=list(setup.calls)
    key,context=decision_context(setup.store,snapshot)
    entry=next(e for e in context['evidence'] if e.get('kind')=='comparison')
    summary=json.loads(entry['text'])
    assert summary['status']==status and summary['chosen_index']==record['chosen_index'] and summary['reason']==record['reason']
    if status=='only_valid':
        assert summary['branches'][0]['status']=='execution_error' and summary['branches'][0]['metric'] is None
        assert sum(r['eligible'] for r in summary['branches'])==1 and '不能' in summary['reason']
    if status=='tied':
        assert summary['chosen_index']==0 and '无实测优胜者' in summary['reason']
    prompt=comparison_writing_prompt(setup);written=comparison_in_prompt(prompt)
    assert written['status']==status and written['reason']==record['reason']
    if status=='only_valid':
        assert written['branches'][0]['status']=='execution_error' and written['branches'][0]['metric'] is None
        assert '不能声称优于失败' in prompt and '不能' in written['reason']
    if status=='tied':
        assert '无实测优胜者' in written['reason'] and written['chosen_index']==0
        assert all(r['metric'] and setup.parent.registry.render(r['metric'])!='' for r in written['branches'])
    basis={'ref':entry['id'],'quote':summary['reason']}
    answer=EvidenceDecision(action='deliver',reason='保留共同协议实测结果与边界，继续原有写作核查。',requirement_ids=['RQ1'],basis=[basis],
        coverage=[{'requirement_id':'RQ1','requirement':snapshot['spec']['requirements'][0]['statement'],
                   'supported':summary['reason'],'basis':[basis],'remaining':''}])
    validate_decision(answer,snapshot,context)
    invalid=answer.model_copy(deep=True);invalid.basis[0].ref='P'+'0'*24
    with pytest.raises(ValueError,match='实际结果'):
        validate_decision(invalid,snapshot,context)
    class Model:
        count=0
        def with_structured_output(self,schema,**kwargs):
            assert schema is EvidenceDecision
            return self
        async def ainvoke(self,messages):
            self.count+=1
            assert all(term in messages[0].content for term in ('winner','only_valid','tied','不能声称优于失败','P比较编号'))
            supplied=json.loads(messages[-1].content)
            assert next(e for e in supplied['evidence'] if e.get('kind')=='comparison')==entry
            return answer
    model=Model();setup.parent.planner_llm=model
    from ai_system.research.loop import resume
    for _ in range(3):
        assert asyncio.run(advance_research(setup.parent)) is False
        setup.store.recover();resume(setup.store)
    assert model.count==1 and decision_context(setup.store,setup.store.snapshot())[0]==key
    assert setup.cfg.budget('Q1')==budget and setup.calls==calls
    assert setup.store.snapshot()['attempts']==snapshot['attempts']


@pytest.mark.parametrize('damage',['prediction_artifact','losing_metric'])
def test_handoff_rejects_changed_comparison_evidence_without_registering_scores(setup,damage):
    from ai_system.modules.experiments.handoff import handoff
    async def work():
        try:
            exploration=await explore(setup.ctx,setup.branch);before=setup.cfg.budget('Q1');calls=list(setup.calls)
            if damage=='prediction_artifact':
                path=setup.ctx.workspace/'outputs/intermediate/comparisons'/exploration.record['id']/'predictions-0.json'
                path.write_bytes(path.read_bytes()+b'\n')
            else:exploration.record['branches'][0]['metric']=0
            with pytest.raises(ValueError,match='比较预测产物已改变|候选正式指标'):
                await handoff(setup.ctx,setup.branch,exploration,[])
            assert not setup.ctx.registry.results
            assert setup.cfg.budget('Q1')==before and setup.calls==calls
        finally:await setup.ctx.kernels.close()
    asyncio.run(work())


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


@pytest.mark.parametrize('first,second,status,index',[(DUMMY_A,DUMMY_B,'tied',0),(BAD_A,RIDGE,'only_valid',1),(BAD_A,BAD_B,'no_valid',None)])
def test_tie_single_failure_and_both_failures_have_complete_records(setup,first,second,status,index):
    setup.codes.update(shallow=first,ridge=second)
    if status=='tied':
        # These are two constant predictors, not a Ridge implementation.
        setup.branch['design']['candidates'][1].update(name='mean alternative',idea='training mean')
        setup.codes['mean alternative']=second
    record=run(setup).to_record();assert record['status']==status and record['chosen_index']==index
    assert len(record['branches'])==2 and all(r['run_ids'] and r.get('elapsed_seconds') is not None for r in record['branches'])
    if index is None:
        assert all(r['error'] for r in record['branches'])
        assert len(setup.cfg.list())==1 and setup.cfg.list()[0]['status']=='no_valid'
    if status=='tied':assert '无实测优胜者' in record['reason']


def test_completed_resume_never_repeats_calls_executions_or_resets_budget(setup):
    first=run(setup).to_record();before=copy.deepcopy(setup.cfg.budget('Q1'))
    setup.store.recover();order=setup.store.claim('Q1');ctx=worker_context(setup.parent,order);setup.ctx=ctx
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


def test_budget_exhaustion_reserves_before_call_and_survives_resume(setup,monkeypatch):
    from ai_system.modules.experiments import store as comparison
    monkeypatch.setitem(comparison.LIMITS,'model_calls',1)
    first=run(setup).to_record();assert first['status']=='budget_exhausted' and first['chosen_index'] is None
    assert len(setup.calls)==1 and setup.cfg.budget('Q1')['used']['model_calls']==1
    second=run(setup).to_record();assert second==first and len(setup.calls)==1


def test_protocol_and_code_cannot_be_edited_after_measurement(setup):
    record=run(setup).to_record()
    altered=copy.deepcopy(record);altered['protocol']['metric']='MAE'
    with pytest.raises(ResearchConflict,match='冻结'):setup.cfg.save(altered)
    altered=copy.deepcopy(record);altered['branches'][0]['code']='different code'
    with pytest.raises(ResearchConflict,match='代码已经冻结'):setup.cfg.save(altered)
    altered=copy.deepcopy(record);altered['branches'][1]['estimator_contract']['alpha']=2
    with pytest.raises(ResearchConflict,match='方法契约已经冻结'):setup.cfg.save(altered)


def test_failed_comparison_enters_existing_issue_and_planner_repair_without_budget_reset(setup):
    setup.codes.update(shallow=BAD_A,ridge=BAD_B);record=run(setup).to_record()
    setup.store.report_comparison_failure(record,setup.order)
    snapshot=setup.store.snapshot();issue=research_findings(snapshot)[0]
    assert issue['source']=='comparison' and issue['status']=='confirmed' and issue['action']=='recompute'
    class Model:
        def with_structured_output(self,*a,**k):return self
        async def ainvoke(self,messages):
            assert record['id'] in messages[-1].content
            return PlanProposal(changes=[TaskChange(task_id='Q1',objective='修复 fit/predict 接口后继续独立比较')],issue_ids=[issue['id']],reason='修复执行契约')
    proposal=asyncio.run(Planner(Model()).propose(snapshot));before=setup.cfg.budget('Q1')
    setup.store.apply_patch(PlanPatch(command_id='repair',base_revision=snapshot['revision'],changes=proposal.changes,issue_ids=proposal.issue_ids,reason=proposal.reason))
    assert setup.cfg.budget('Q1')==before and setup.store.snapshot()['tasks']['Q1']['revision']==2
    assert setup.cfg.list()[0]['branches'][0]['run_ids']


def test_metric_contracts_and_splits_are_project_owned(setup):
    assert engine.metric_value('MAE',[1,2],[2,4])==1.5
    assert engine.metric_value('RMSE',[1,2],[2,4])==pytest.approx((2.5)**.5)
    assert engine.metric_value('AUC',[0,0,1,1],[.1,.2,.8,.9])==1
    with pytest.raises(ValueError):engine.metric_value('RMSE',[1,2],[1])
    with pytest.raises(ValueError):SupervisedSpec(**{**setup.spec,'features':['target']})
    with pytest.raises(ValueError):SupervisedSpec(**{**setup.spec,'task_type':'binary','metric':'RMSE'})
    frozen,*_=engine.prepare(setup.ctx.workspace,setup.spec)
    assert frozen['data_hash']==setup.store.file_inputs()['data/diabetes.csv']
    assert frozen['negative_permutation_indices']!=list(range(442))


def test_duplicate_code_is_rejected_before_any_execution(setup):
    setup.codes.update(shallow=RIDGE,ridge=RIDGE)
    record=run(setup).to_record();assert record['status']=='no_valid'
    assert all(r['status']=='contract_error' and not r['run_ids'] for r in record['branches'])
    assert setup.cfg.budget('Q1')['used']['executions']==0


def test_interrupted_generation_consumes_reservation_and_is_not_repeated(setup):
    frozen,*_=engine.prepare(setup.ctx.workspace,setup.spec)
    record=setup.cfg.create('Q1',1,{'status':'frozen','candidate_hashes':[digest(c) for c in setup.branch['design']['candidates']],
        'file_inputs':setup.store.file_inputs(),'input_refs':setup.order.input_refs,'input_digest':setup.order.input_digest,
        'contract_hash':setup.order.protocol_hash,'policy_version':setup.cfg.policy()['version'],'protocol':frozen,'protocol_hash':digest(frozen),
        'branches':[{'index':i,'name':c['name'],'status':'generating' if i==0 else 'pending','run_ids':[]} for i,c in enumerate(setup.branch['design']['candidates'])]})
    assert setup.cfg.reserve('Q1','model_calls',25,record['id']+':code:0',candidate=True)
    result=run(setup).to_record()
    assert result['branches'][0]['status']=='interrupted' and not result['branches'][0]['run_ids']
    assert setup.calls==['ridge'] and result['status']=='only_valid'
    assert setup.cfg.budget('Q1')['used']['model_calls']==2


def test_changed_input_before_comparison_uses_no_extra_budget(setup):
    path=setup.root/'data/diabetes.csv';path.write_text(path.read_text()+'\n')
    result=run(setup)
    assert result.record['status']=='input_changed' and result.winner is None
    assert not setup.calls and setup.cfg.budget('Q1')['used']['executions']==0


def test_group_and_time_split_keep_actual_ids_and_groups_separate(setup):
    import pandas as pd
    path=setup.ctx.workspace/'data/diabetes.csv';data=pd.read_csv(path)
    data['group']=np.arange(len(data))%5;data['time']=pd.date_range('2020-01-01',periods=len(data)).astype(str);data.to_csv(path,index=False)
    groups,*_=engine.prepare(setup.ctx.workspace,{**setup.spec,'split':'group','split_column':'group'})
    indexed=data.set_index('sample_id')
    assert not set(indexed.loc[groups['train_ids'],'group'])&set(indexed.loc[groups['validation_ids'],'group'])
    timed,*_=engine.prepare(setup.ctx.workspace,{**setup.spec,'split':'time','split_column':'time'})
    assert max(indexed.loc[timed['train_ids'],'time'])<min(indexed.loc[timed['validation_ids'],'time'])


def test_automatic_schema_protocol_is_one_reserved_call_without_validation_labels(setup,monkeypatch):
    setup.cfg.set_policy(True,None);original=engine.ask;protocol_calls=[]
    async def schema(ctx,schema,system,prompt,timeout,tokens=2000):
        if schema is SupervisedSpec:
            payload=json.loads(prompt);assert set(payload)=={'question','files'}
            assert all(set(f)=={'path','columns'} for f in payload['files'])
            protocol_calls.append(1);return SupervisedSpec(**setup.spec)
        return await original(ctx,schema,system,prompt,timeout,tokens)
    monkeypatch.setattr(engine,'ask',schema)
    record=run(setup).to_record();assert record['status']=='winner' and protocol_calls==[1]
    assert setup.cfg.budget('Q1')['used']['model_calls']==3


def test_failed_comparison_blocks_formal_solver_and_commits_complete_failure_record(setup,monkeypatch):
    from ai_system.research.control import execute
    setup.codes.update(shallow=BAD_A,ridge=BAD_B);setup.store.recover()
    async def forbidden(*args,**kwargs):pytest.fail('no-valid experiments cannot fall back to a formal model self-score')
    import importlib
    solve_module=importlib.import_module('ai_system.modules.experiments.execution')
    monkeypatch.setattr(solve_module,'_solve_loop',forbidden)
    asyncio.run(execute(setup.branch,SimpleNamespace(context=setup.parent)))
    records=setup.cfg.list();assert len(records)==1 and records[0]['status']=='no_valid'
    assert all(r['run_ids'] and r['error_detail'] for r in records[0]['branches'])
    assert not setup.store.registry_payload()['results']
    snapshot=setup.store.snapshot()
    assert any(i['source']=='comparison' and i['action']=='recompute' for i in research_findings(snapshot))
    attempted=list(snapshot['attempts'].values())[-1]
    assert attempted['result']['report']['status']=='comparison_blocked'


def test_winner_kernel_and_export_retain_real_training_model(setup):
    from ai_system.modules.experiments.execution import export_solution_code
    async def work():
        try:
            result=await explore(setup.ctx,setup.branch)
            assert result.winner and result.record['kernel_promoted']
            assert set(setup.ctx.kernels._kernels)=={'Q1'}
            export_solution_code(setup.ctx,'Q1',result.winner.run_log)
            code=(setup.ctx.workspace/'code/Q1_solution.py').read_text()
            assert 'comparison_estimator' in code and 'outputs/intermediate/comparisons' in code
            inspected=await setup.ctx.kernels.get('Q1').execute("print(comparison_estimator.predict(_V)[:1].tolist()==comparison_predictions[:1])")
            assert inspected.ok and 'True' in inspected.stdout
        finally:await setup.ctx.kernels.close()
    asyncio.run(work())


def test_two_candidate_mode_generates_exactly_two_distinct_allowed_plans(setup,monkeypatch):
    from ai_system.modules.experiments import planning as design
    from ai_system.graph.state import CandidateSet,ModelCandidate
    setup.ctx.n_candidates=1;seen=[]
    async def generate(llm,schema,messages,**kwargs):
        assert '必须恰好给出两项' in messages[-1].content
        result=CandidateSet(candidates=[ModelCandidate(**c) for c in setup.branch['design']['candidates']])
        kwargs['validate'](result);seen.append(result);return result
    monkeypatch.setattr(design,'call_structured',generate)
    plans=asyncio.run(design.propose(setup.ctx,setup.branch,['gbdt','regression']))
    assert len(plans)==2 and len(seen)==1
    setup.branch['design']['candidates'][1]=copy.deepcopy(setup.branch['design']['candidates'][0])
    with pytest.raises(ValueError,match='两项不同方案'):asyncio.run(design.propose(setup.ctx,setup.branch,['gbdt']))


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


def test_missing_second_plan_is_visible_and_never_executes_a_single_plan_fallback(setup):
    setup.branch['design']['candidates']=setup.branch['design']['candidates'][:1]
    result=run(setup)
    assert result.winner is None and result.record['status']=='no_valid'
    assert len(result.record['branches'])==2 and result.record['branches'][1]['status']=='not_generated'
    assert setup.calls==[]
    setup.store.report_comparison_failure(result.record,setup.order)
    assert research_findings(setup.store.snapshot())[0]['source']=='comparison'
