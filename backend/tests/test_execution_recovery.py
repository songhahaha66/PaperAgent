"""Self-contained real sklearn computations; no gateway or substitute API calls."""

from ai_system.research.domain import findings as research_findings
import contextlib,io,json,sys,hashlib
from pathlib import Path
from types import SimpleNamespace
import numpy as np,pandas as pd,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sklearn.linear_model import LinearRegression,Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from ai_system.research.store import ResearchStore,digest
from ai_system.research.validation import protocol,report_fingerprint
from ai_system.modules.experiments.store import ExperimentStore,SupervisedSpec
from ai_system.research.models import WorkerResult,IssueReport,ReviewRecord
from ai_system.research.execution_recovery import preview,resolve,REFUSAL
from ai_system.modules.experiments.supervised import prepare,metric_value
from ai_system.modules.experiments.contracts import check_estimator,candidate_contract

COMPUTE='''Xtr=np.asarray(_data['X_train']); Xva=_V.to_numpy(); ytr=np.asarray(_formal_train); yva=np.asarray(_formal_truth)
beta,*_=np.linalg.lstsq(np.column_stack([np.ones(len(Xtr)),Xtr]),ytr,rcond=None)
ols_pred=np.column_stack([np.ones(len(Xva)),Xva])@beta
rmse_ols_manual=np.sqrt(np.mean((yva-ols_pred)**2))
mu=Xtr.mean(axis=0); sd=Xtr.std(axis=0); Ztr=(Xtr-mu)/sd; Zva=(Xva-mu)/sd
weights=np.linalg.solve(Ztr.T@Ztr+np.eye(Ztr.shape[1]),Ztr.T@(ytr-ytr.mean()))
ridge_pred=Zva@weights+ytr.mean()
rmse_ridge=np.sqrt(np.mean((yva-ridge_pred)**2))
print('rmse_ols_manual',rmse_ols_manual)
print('rmse_ridge',rmse_ridge)
print('max_abs_diff_vs_formal_pred',np.max(np.abs(ols_pred-_formal_pred)))
'''

@pytest.fixture
def recovered(tmp_path):
    root=tmp_path/'study';(root/'data').mkdir(parents=True)
    x=np.linspace(-2,2,80);pd.DataFrame({'x1':x,'x2':np.cos(np.arange(80)*.37),'target':3*x-np.cos(np.arange(80)*.37)+.05*np.sin(4*x)}).to_csv(root/'data/sample.csv',index=False)
    store=ResearchStore(root,'study')
    source='检查RMSE是有限数。'
    analysis={'subproblems':[{'id':'Q1','title':'两项回归','question':source,'depends_on':[]}],
      'requirements':[{'id':'RQ1','kind':'constraint','statement':source,'source_quote':source,'task_ids':['Q1'],
        'checks':[{'id':'finite','description':'有限误差','left':{'task_id':'Q1','name':'comparison_rmse'},'operator':'finite'}]}],'assumptions':[]}
    store.initialize(source,analysis);order=store.claim('Q1');aid=order.attempt_id
    workspace=root/'.system/attempts'/aid/'workspace';folder=workspace/'outputs/intermediate/comparisons/Ptest';folder.mkdir(parents=True)
    spec=SupervisedSpec(dataset='data/sample.csv',features=['x1','x2'],target='target',seed=42).model_dump()
    frozen,payload,truth,_,train,_=prepare(root,spec);X=pd.DataFrame(payload['X_train'],columns=payload['columns']);V=pd.DataFrame(payload['X_validation'],columns=payload['columns'])
    scope={'np':np,'pd':pd,'X':X,'V':V,'train':train};manifest={'runs':{}}
    def run(rid,code):
        directory=root/'runs'/rid;directory.mkdir(parents=True,exist_ok=True)
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream):exec(code,scope)
        (directory/'input.py').write_text(code);(directory/'stdout.log').write_text(stream.getvalue())
        manifest['runs'][rid]={'status':'success','input':f'runs/{rid}/input.py','stdout':f'runs/{rid}/stdout.log'}
        return stream.getvalue()
    branches=[];candidates=[]
    for index,(name,factory,expression) in enumerate([('OLS',lambda:LinearRegression(),'LinearRegression()'),('标准化Ridge',lambda:make_pipeline(StandardScaler(),Ridge(alpha=1)),'make_pipeline(StandardScaler(),Ridge(alpha=1))')]):
        candidate={'name':name,'idea':'alpha=1' if index else 'OLS','steps':[]}
        candidates.append(candidate)
        estimator=factory();audit=check_estimator(estimator,candidate_contract(candidate))
        scope.update(LinearRegression=LinearRegression,Ridge=Ridge,make_pipeline=make_pipeline,StandardScaler=StandardScaler)
        driver=f'model={expression}\nmodel.fit(X,train)\npred=model.predict(V)\nprint("predictions_saved")\n'
        run('candidate'+str(index),driver);pred=scope['pred'].copy();blob=json.dumps({'prediction':pred.tolist(),'estimator_audit':audit}).encode();(folder/f'predictions-{index}.json').write_bytes(blob)
        branches.append({'index':index,'name':name,'status':'valid','code_hash':digest(expression),'driver_hash':hashlib.sha256(driver.encode()).hexdigest(),
          'implementation_audit':audit,'predictions':[{'prediction':float(p)} for p in pred],'prediction_hash':hashlib.sha256(blob).hexdigest(),'metric':metric_value('RMSE',truth,pred),'run_ids':['candidate'+str(index)]})
        (folder/f'inputs-{index}.json').write_text(json.dumps(payload))
    cfg=ExperimentStore(store);cfg.set_policy(True,spec)
    record=cfg.create('Q1',1,{'status':'winner','chosen_index':0,'owner_attempt':aid,'implementation_contract_version':1,'contract_hash':digest(protocol(store.snapshot(),'Q1')),
      'policy_version':cfg.policy()['version'],'candidate_hashes':[digest(c) for c in candidates],'task_hash':digest(store.snapshot()['tasks']['Q1']['subproblem']),'input_refs':order.input_refs,
      'file_inputs':store.file_inputs(),'protocol':frozen,'protocol_hash':digest(frozen),'branches':branches})
    assert record['id']!='Ptest'
    folder.rename(folder.parent/record['id'])
    scope.update(_data=payload,_V=V,_formal_train=train,_formal_truth=truth,_formal_pred=np.array([p['prediction'] for p in branches[0]['predictions']]))
    out=run('computed',COMPUTE)
    alias_code="print('comparison_rmse',rmse_ols_manual)\nprint('rmse_ols',rmse_ols_manual)\nprint('rmse_ridge',rmse_ridge)\n"
    alias=run('registered',alias_code);(root/'manifest.json').write_text(json.dumps(manifest))
    refused_code="ols=LinearRegression().fit(X,train)\nridge=make_pipeline(StandardScaler(),Ridge(alpha=1)).fit(X,train)"
    def pair(identifier,code,content):return [{'type':'ai','data':{'tool_calls':[{'id':identifier,'name':'run_python','args':{'code':code}}]}},{'type':'tool','data':{'tool_call_id':identifier,'content':content}}]
    transcript=pair('refused',refused_code,REFUSAL)+pair('computed_call',COMPUTE,'[run_id: computed]\n'+out)+pair('registered_call',alias_code,'[run_id: registered]\n'+alias)
    path=workspace/'.system/transcripts/Q1.json';path.parent.mkdir(parents=True);path.write_text(json.dumps(transcript))
    values=[('R1','comparison_rmse',scope['rmse_ols_manual']),('R2','rmse_ols',scope['rmse_ols_manual']),('R3','rmse_ridge',scope['rmse_ridge'])]
    results=[{'id':rid,'kind':'number','subproblem':'Q1','name':name,'value':str(float(value)),'run_id':'registered','path':'','unit':'','description':''} for rid,name,value in values]
    issue=IssueReport(task_id='Q1',category='execution',message='冻结保护拒绝重新fit，后续进行闭式核对',evidence_refs=['R2','R3'])
    other=IssueReport(task_id='Q1',category='execution',message='另一个文件导出仍然失败',evidence_refs=[])
    report={'status':'completed','summary':'已完成原方法复核','research_issues':[issue.model_dump()],
       'requirement_evidence':[{'requirement_id':'RQ1','evidence_refs':['R1']}]}
    store.commit(WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',results=results,report=report,issues=[issue,other],design={'candidates':candidates},
        reviews=[ReviewRecord(report_hash=report_fingerprint(report),issues=[])]))
    selected=next(i for i in research_findings(store.snapshot()) if i['message']==issue.message)
    return SimpleNamespace(store=store,issue=selected,path=path,transcript=transcript,run=run,manifest=manifest)


def test_explicit_resolution_preserves_issue_and_other_real_failure(recovered):
    store=recovered.store;before=store.snapshot();proof=preview(store,recovered.issue['id'])
    assert proof['refusal']['kernel_executed'] is False and proof['successful_computation']['run_id']=='computed'
    assert {p['outcome'] for p in proof['validation']['checks']}=={'passed'}
    resolve(store,recovered.issue['id'],proof['proof_hash']);after=store.snapshot()
    issue=next(i for i in research_findings(after) if i['id']==recovered.issue['id'])
    assert issue['status']=='resolved' and issue['message']==recovered.issue['message'] and issue['evidence_refs']==['R2','R3']
    assert issue['resolution']==proof and not store.delivery_ready(after)
    assert next(i for i in research_findings(after) if i['id']!=issue['id'])['status']=='confirmed'
    seq=after['commit_seq'];resolve(store,issue['id'],proof['proof_hash']);assert store.snapshot()['commit_seq']==seq
    assert after['attempts']==before['attempts'] and after['validations']==before['validations']


@pytest.mark.parametrize('change',['no_recovery','constants_only','contract_changed','input_changed','revision_changed','ambiguous_refusal','experiment_policy_changed','candidate_changed'])
def test_unrecovered_or_changed_context_never_closes(recovered,change):
    store=recovered.store
    if change=='no_recovery':recovered.path.write_text(json.dumps(recovered.transcript[:2]+recovered.transcript[4:]))
    elif change=='constants_only':
        code="print('rmse_ols_manual',0.0)\nprint('rmse_ridge',0.0)\n"
        recovered.run('computed',code);messages=recovered.transcript;messages[2]['data']['tool_calls'][0]['args']['code']=code;recovered.path.write_text(json.dumps(messages))
    elif change=='input_changed':(store.root/'data/sample.csv').write_text('x1,x2,target\n1,2,3\n')
    elif change=='ambiguous_refusal':recovered.path.write_text(json.dumps(recovered.transcript[:2]+recovered.transcript))
    elif change=='experiment_policy_changed':ExperimentStore(store).set_policy(False)
    else:
        with store._transaction() as db:
            state=store._read(db)
            if change=='contract_changed':state['spec']['requirements'][0]['checks'][0]['description']='changed frozen check'
            elif change=='candidate_changed':state['tasks']['Q1']['design']['candidates'][0]['idea']='another method'
            else:state['tasks']['Q1']['revision']+=1
            store._save(db,state,'test_context_changed',{})
    with pytest.raises(ValueError):preview(store,recovered.issue['id'])
    assert next(i for i in research_findings(store.snapshot()) if i['id']==recovered.issue['id'])['status']=='confirmed'


def test_source_change_after_preview_cannot_apply_old_proof(recovered):
    proof=preview(recovered.store,recovered.issue['id'])
    path=recovered.store.root/'runs/computed/stdout.log';path.write_text('no recovery data')
    with pytest.raises(ValueError):resolve(recovered.store,recovered.issue['id'],proof['proof_hash'])
    assert research_findings(recovered.store.snapshot())[0]['status']=='confirmed'


def test_additional_failed_kernel_run_is_not_treated_as_recovered(recovered):
    messages=recovered.transcript
    messages.extend([{'type':'ai','data':{'tool_calls':[{'id':'failed','name':'run_python','args':{'code':'raise ValueError()'}}]}},
        {'type':'tool','data':{'tool_call_id':'failed','content':'[run_id: failed_run]\nTraceback'}}])
    recovered.path.write_text(json.dumps(messages))
    recovered.manifest['runs']['failed_run']={'status':'error'}
    (recovered.store.root/'manifest.json').write_text(json.dumps(recovered.manifest))
    with pytest.raises(ValueError,match='失败内核运行'):preview(recovered.store,recovered.issue['id'])


def test_policy_change_inside_resolution_transaction_rolls_back(recovered,monkeypatch):
    store=recovered.store;proof=preview(store,recovered.issue['id']);original=store._transaction
    @contextlib.contextmanager
    def changed():
        with original() as db:
            db.execute('UPDATE experiment_policy SET version=version+1 WHERE id=1')
            yield db
    monkeypatch.setattr(store,'_transaction',changed)
    with pytest.raises(ValueError,match='关闭前比较配置'):resolve(store,recovered.issue['id'],proof['proof_hash'])
    assert research_findings(store.snapshot())[0]['status']=='confirmed'
