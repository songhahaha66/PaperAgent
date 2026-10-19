"""Auditable formal handoff of the exact frozen estimator, separate from model prose."""
import hashlib
import json
from dataclasses import asdict
import numpy as np
from .supervised import prepare,root_store,current
from ai_system.modules.experiments.store import ExperimentStore
from ...research.store import digest


async def handoff(ctx,branch,exploration,run_log):
    record=exploration.record;row=record['branches'][record['chosen_index']]
    from .contracts import VERSION,candidate_contract
    candidates=branch['design'].get('candidates',[])
    if record.get('implementation_contract_version')!=VERSION and any(candidate_contract(c) for c in candidates):
        raise ValueError('旧比较未核查实际估计器的方法契约；须明确修订当前任务后重做比较，旧记录保留')
    for item in record['branches']:
        if item.get('estimator_contract') and item.get('status')=='valid' and not item.get('implementation_audit',{}).get('checked'):
            raise ValueError('比较缺少项目实际估计器核查，禁止正式交接')
    if not current(ctx,record,branch):raise ValueError('正式求解前冻结输入失效')
    frozen,payload,truth,_,train,_=prepare(ctx.workspace,record['protocol'])
    if digest(frozen)!=record['protocol_hash']:raise ValueError('正式求解协议不匹配')
    sid=branch['subproblem']['id'];live=sid in ctx.kernels._kernels
    folder=ctx.workspace/'outputs/intermediate/comparisons'/record['id'];folder.mkdir(parents=True,exist_ok=True)
    module=folder/'formal_estimator.py';inputs=folder/'formal_inputs.json';output=folder/'formal_metrics.json'
    module.write_text(row['code']);inputs.write_text(json.dumps(payload,allow_nan=False))
    rel=lambda path:path.relative_to(ctx.workspace).as_posix()
    prefix='' if live else f'''import json,numpy as np,pandas as pd
from pathlib import Path
_handoff_data=json.loads(Path({rel(inputs)!r}).read_text())
_handoff_scope={{}}
exec(compile(Path({rel(module)!r}).read_text(),'<frozen-formal-estimator>','exec'),_handoff_scope)
_X=pd.DataFrame(_handoff_data['X_train'],columns=_handoff_data['columns'])
_V=pd.DataFrame(_handoff_data['X_validation'],columns=_handoff_data['columns'])
comparison_estimator=_handoff_scope['make_estimator'](_handoff_data['seed'])
comparison_estimator.fit(_X,np.asarray(_handoff_data['y_train']))
'''
    code=prefix+f'''import json,numpy as np,math
from pathlib import Path
from sklearn.metrics import mean_absolute_error,mean_squared_error,roc_auc_score
_formal_truth=np.asarray({truth.tolist()!r})
_formal_train=np.asarray({train.tolist()!r})
if {frozen['task_type']!r}=='binary' and hasattr(comparison_estimator,'predict_proba'):
    _formal_pred=comparison_estimator.predict_proba(_V)[:,list(comparison_estimator.classes_).index(1)]
elif {frozen['task_type']!r}=='binary' and hasattr(comparison_estimator,'decision_function'):
    _formal_pred=comparison_estimator.decision_function(_V)
else:
    _formal_pred=comparison_estimator.predict(_V)
def _formal_score(y,p):
    return float(roc_auc_score(y,p) if {frozen['metric']!r}=='AUC' else mean_absolute_error(y,p) if {frozen['metric']!r}=='MAE' else math.sqrt(mean_squared_error(y,p)))
_formal_baseline=np.full(len(_formal_truth),np.median(_formal_train) if {frozen['metric']!r}=='MAE' else np.mean(_formal_train))
_formal_values={{'metric':_formal_score(_formal_truth,_formal_pred),'baseline':_formal_score(_formal_truth,_formal_baseline),'train_count':len(_formal_train),'validation_count':len(_formal_truth),'total_count':len(_formal_train)+len(_formal_truth)}}
Path({rel(output)!r}).write_text(json.dumps({{'values':_formal_values,'predictions':np.asarray(_formal_pred).tolist()}},allow_nan=False))
for _name,_value in _formal_values.items():print(_name,_value)
'''
    result=await ctx.kernels.get(sid).execute(code);run_log.append({'run_id':result.run_id,'code':code,'ok':result.ok})
    if not result.ok:raise ValueError('冻结模型的正式证据复核执行失败')
    actual=json.loads(output.read_text());expected=[p['prediction'] for p in row['predictions']]
    if not np.allclose(actual['predictions'],expected,rtol=1e-9,atol=1e-10) or not np.isclose(actual['values']['metric'],row['metric'],rtol=1e-9,atol=1e-10) or not np.isclose(actual['values']['baseline'],row['baseline'],rtol=1e-9,atol=1e-10):
        raise ValueError('正式模型与冻结胜者的预测不一致，禁止沿用比较结论')
    metric_name='comparison_'+frozen['metric'].lower();protected={}
    names={'metric':metric_name,'baseline':'comparison_baseline_'+frozen['metric'].lower(),'train_count':'comparison_train_count','validation_count':'comparison_validation_count','total_count':'comparison_total_count'}
    for key,name in names.items():
        entry=ctx.registry.record_value(sid,name,str(actual['values'][key]),result.run_id,description='冻结方案的项目独立正式复核')
        protected[entry.id]=asdict(entry)
    proof={'comparison_id':record['id'],'code_hash':row['code_hash'],'protocol_hash':record['protocol_hash'],'run_id':result.run_id,
        'artifact_hash':hashlib.sha256(output.read_bytes()).hexdigest(),'protected':protected,'fresh_fit':not live}
    ctx.comparison_proof=proof
    record.setdefault('formal_handoffs',[]).append({**{k:v for k,v in proof.items() if k!='protected'},'evidence_refs':list(protected)})
    ExperimentStore(root_store(ctx)).save(record)
    return '\n项目已按冻结代码复核并登记当前研究证据，必须复用 comparison_estimator，不得重新 fit、调参或另换模型；可绘制现有 _formal_truth 与 _formal_pred。\n'+ctx.registry.to_prompt_table(sid)


def proof_issues(ctx,report):
    proof=ctx.comparison_proof
    if not proof:return []
    issues=[]
    for rid,expected in proof['protected'].items():
        actual=ctx.registry.results.get(rid)
        if actual is None or asdict(actual)!=expected:issues.append('冻结方案正式证据被替换或丢失：'+rid)
    evidence={rid for p in report.get('requirement_evidence',[]) for rid in p.get('evidence_refs',[])}
    required={rid for rid,row in proof['protected'].items() if row['name'].startswith(('comparison_rmse','comparison_mae','comparison_auc','comparison_baseline'))}
    if not required<=evidence:issues.append('正式报告必须引用项目复核的冻结指标与共同基线：'+','.join(sorted(required)))
    return issues
