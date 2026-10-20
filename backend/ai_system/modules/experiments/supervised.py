"""Project-owned supervised experiments; candidate stdout never selects a winner.

Generated Python is trusted to follow fit/predict: this is not an adversarial sandbox.
Validation targets are kept out of generation prompts and the fit/predict payload.
"""
import ast
import asyncio
import hashlib
import json
import math
import re
import time
import inspect
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error,mean_squared_error,roc_auc_score
from sklearn.model_selection import train_test_split,GroupShuffleSplit

from ai_system.modules.experiments.store import ExperimentStore,SupervisedSpec,CandidateCode
from ...research.store import ResearchStore,digest
from ...research.validation import protocol as research_protocol
from ai_system.graph.llm_utils import as_messages,structured_model,cap_output
from .contracts import candidate_contract,check_estimator,VERSION as CONTRACT_VERSION

TERMINAL={'winner','tied','only_valid','no_valid','budget_exhausted','input_changed','not_applicable'}


def csv_files(root):
    root=Path(root).resolve()
    return [p for name in ('data','attachment') for p in (root/name).rglob('*.csv') if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root)][:20]


def metadata(root):
    result=[]
    for p in csv_files(root):
        if p.stat().st_size>10_000_000:
            result.append({'path':p.relative_to(root).as_posix(),'columns':{},'error':'超过 10 MB 限制'});continue
        try:frame=pd.read_csv(p,nrows=10)
        except (ValueError,OSError,pd.errors.ParserError):
            result.append({'path':p.relative_to(root).as_posix(),'columns':{},'error':'CSV 无法读取'});continue
        result.append({'path':p.relative_to(root).as_posix(),'columns':{c:str(t) for c,t in frame.dtypes.items()}})
    return result


def prepare(root,spec):
    root=Path(root).resolve();path=(root/spec['dataset']).resolve()
    if not any(path.is_relative_to(root/name) for name in ('data','attachment')) or path.suffix.lower()!='.csv' or not path.is_file() or path.stat().st_size>10_000_000:raise ValueError('比较仅支持工作区 data/attachment 内不超过 10 MB 的 CSV')
    raw=path.read_bytes();frame=pd.read_csv(path)
    if not 20<=len(frame)<=20000:raise ValueError('首版比较支持 20 至 20000 个样本')
    wanted=[spec['target'],*spec['features'],*([spec['id_column']] if spec['id_column'] else []),*([spec['split_column']] if spec['split_column'] else [])]
    if not set(wanted)<=set(frame.columns):raise ValueError('协议列不存在于当前 CSV')
    if frame[spec['target']].isna().any():raise ValueError('目标列存在缺失值，需先明确处理策略')
    ids=frame[spec['id_column']].astype(str).tolist() if spec['id_column'] else [f'row:{i}' for i in range(len(frame))]
    if len(set(ids))!=len(ids) or spec['id_column'] and frame[spec['id_column']].isna().any():raise ValueError('样本 ID 必须唯一且非空')
    if spec['task_type']=='binary':
        labels=sorted(frame[spec['target']].astype(str).unique().tolist())
        if len(labels)!=2:raise ValueError('AUC 首版只支持二分类目标')
        y=(frame[spec['target']].astype(str)==labels[1]).astype(int).to_numpy()
    else:
        labels=None;y=pd.to_numeric(frame[spec['target']],errors='raise').to_numpy(dtype=float)
        if not np.isfinite(y).all():raise ValueError('回归目标必须为有限数')
    rows=np.arange(len(frame));fraction=spec['validation_fraction'];seed=spec['seed']
    if spec['split']=='group':
        groups=frame[spec['split_column']]
        if groups.isna().any() or groups.nunique()<3:raise ValueError('分组划分需要至少三个非空组')
        train,valid=next(GroupShuffleSplit(n_splits=1,test_size=fraction,random_state=seed).split(rows,groups=groups))
    elif spec['split']=='time':
        values=frame[spec['split_column']]
        if values.isna().any():raise ValueError('时间列不允许缺失')
        if not pd.api.types.is_numeric_dtype(values):frame[spec['split_column']]=pd.to_datetime(values,errors='raise',utc=True)
        ordered=frame.sort_values(spec['split_column'],kind='stable').index.to_numpy();cut=int(len(rows)*(1-fraction))
        boundary=frame.loc[ordered[cut],spec['split_column']]
        train=ordered[frame.loc[ordered,spec['split_column']].to_numpy()<boundary]
        valid=ordered[frame.loc[ordered,spec['split_column']].to_numpy()>=boundary]
    else:
        train,valid=train_test_split(rows,test_size=fraction,random_state=seed,stratify=y if spec['task_type']=='binary' else None)
    if len(train)<5 or len(valid)<5 or len(valid)>5000:raise ValueError('划分后样本数量不适用')
    if spec['task_type']=='binary' and (len(set(y[train]))<2 or len(set(y[valid]))<2):raise ValueError('AUC 训练与验证均需包含两个类别')
    permutation=np.random.default_rng(seed+1).permutation(rows);perm=y[permutation]
    features=frame[spec['features']].replace([np.inf,-np.inf],np.nan).astype(object)
    features=features.where(pd.notna(features),None)
    split={**spec,'data_hash':hashlib.sha256(raw).hexdigest(),'train_ids':[ids[i] for i in train],
        'validation_ids':[ids[i] for i in valid],'train_indices':train.tolist(),'validation_indices':valid.tolist(),
        'positive_label':labels[1] if labels else None,'higher_is_better':spec['metric']=='AUC',
        'baseline':'training_prior' if spec['task_type']=='binary' else 'training_median' if spec['metric']=='MAE' else 'training_mean',
        'negative_control':'permute_all_targets_before_split','feature_dtypes':{c:str(frame[c].dtype) for c in spec['features']}}
    split.update(negative_permutation_indices=permutation.tolist(),baseline_value=baseline_value(spec['metric'],y[train],y[valid]),
        negative_baseline_value=baseline_value(spec['metric'],perm[train],perm[valid]))
    payload={'X_train':features.iloc[train].values.tolist(),'X_validation':features.iloc[valid].values.tolist(),
        'columns':spec['features'],'y_train':y[train].tolist(),'permuted_y_train':perm[train].tolist(),
        'task_type':spec['task_type'],'seed':seed}
    # y_validation is only in the coordinator, never in the candidate API payload.
    return split,payload,y[valid],perm[valid],y[train],perm[train]


def metric_value(metric,truth,prediction):
    pred=np.asarray(prediction,dtype=float)
    if pred.ndim!=1 or len(pred)!=len(truth) or not np.isfinite(pred).all():raise ValueError('预测必须是等长、有限的一维数组')
    return float(roc_auc_score(truth,pred) if metric=='AUC' else mean_absolute_error(truth,pred) if metric=='MAE' else math.sqrt(mean_squared_error(truth,pred)))


def baseline_value(metric,train,truth):
    value=np.mean(train) if metric!='MAE' else np.median(train)
    return metric_value(metric,truth,np.full(len(truth),value))


def choose(record):
    protocol=record['protocol'];rows=record['branches']
    valid=[r for r in rows if r['status']=='valid']
    if not valid:
        return 'no_valid',None,'两支均未产生满足冻结规则的指标；未使用模型自评分选择'
    eligible=[r for r in valid if r['improvement']>=protocol['minimum_improvement']]
    if not eligible:return 'no_valid',None,'两支均未达到共同基线的最低改善量；这是分数结果，不证明方法错误'
    # Old formal handoff receipts retain their representative index. New task
    # intents return all observations to observe without promoting a candidate.
    if len(eligible)==1:return 'only_valid',None if record.get('intent_hash') else eligible[0]['index'],'仅一支满足冻结规则和基线门槛；保留候选，不把另一支执行失败视为方法无效，也不据此判定赢家'
    a,b=sorted(eligible,key=lambda r:r['index']);delta=abs(a['metric']-b['metric'])
    if delta<=protocol['tie_tolerance']:return 'tied',None if record.get('intent_hash') else a['index'],'实测差异在冻结容差内；保留两个候选，可继续补实验，无实测优胜者'
    best=max(eligible,key=lambda r:r['metric']) if protocol['higher_is_better'] else min(eligible,key=lambda r:r['metric'])
    return 'winner',best['index'],f"按项目重算 {protocol['metric']} 选择候选 {best['index']+1}，未使用 critic 或 stdout 分数"


def root_store(ctx):
    return ctx.experience_store or ctx.research or ResearchStore(ctx.source_workspace or ctx.workspace,'workspace-comparisons')


def current(ctx,record,branch):
    store=root_store(ctx);state=store.snapshot();order=ctx.experience_order
    candidates=branch['design'].get('candidates',[])[:2]
    if [digest(c) for c in candidates]!=record['candidate_hashes']:return False
    if record.get('intent_hash') and digest(branch.get('experiment') or branch['design'].get('experiment'))!=record['intent_hash']:return False
    if record.get('task_hash',digest(branch['subproblem']))!=digest(branch['subproblem']):return False
    if store.file_inputs()!=record['file_inputs']:return False
    if state and order:
        task=state['tasks'][order.task_id]
        if task['revision']!=record['revision'] or digest(research_protocol(state,order.task_id))!=record['contract_hash']:return False
        if task['active_attempt']!=order.attempt_id or state['attempts'][order.attempt_id]['lease']!=order.lease:return False
        if store._inputs(state,task,store.file_inputs())!=order.input_refs:return False
    if record.get('policy_version') is not None and ExperimentStore(store).policy()['version']!=record['policy_version']:return False
    return True


def valid_code(code):
    tree=ast.parse(code)
    if not any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='make_estimator' for n in tree.body):raise ValueError('候选必须定义 make_estimator(seed)')
    return digest(ast.dump(tree,include_attributes=False))


def diagnostic(value):
    text=str(value or '')[:1800]
    text=re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+',r'\1[REDACTED]',text)
    return re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|authorization|sa[_-]?token)\s*[:=]\s*)[^\s,;]+',r'\1[REDACTED]',text)


DRIVER='''
import json, numpy as np, pandas as pd
from pathlib import Path
_data=json.loads(Path({payload!r}).read_text())
_scope={{}}
exec(compile(Path({code!r}).read_text(),'<frozen-candidate>','exec'),_scope)
_X=pd.DataFrame(_data['X_train'],columns=_data['columns'])
_V=pd.DataFrame(_data['X_validation'],columns=_data['columns'])
_outputs={{}}
_models={{}}
{guard}
for _name,_label in [('prediction','y_train'),('negative_prediction','permuted_y_train')]:
    _model=_scope['make_estimator'](_data['seed'])
    _audit=check_estimator(_model,{contract!r})
    if _name=='prediction':_outputs['estimator_audit']=_audit
    _model.fit(_X,np.asarray(_data[_label]))
    if _data['task_type']=='binary' and hasattr(_model,'predict_proba'):
        _pred=_model.predict_proba(_V)[:,list(_model.classes_).index(1)]
    elif _data['task_type']=='binary' and hasattr(_model,'decision_function'):
        _pred=_model.decision_function(_V)
    else:
        _pred=_model.predict(_V)
    _outputs[_name]=np.asarray(_pred).tolist()
    _models[_name]=_model
comparison_estimator=_models['prediction']
comparison_predictions=_outputs['prediction']
Path({output!r}).write_text(json.dumps(_outputs,allow_nan=False))
print('PROJECT_PREDICTIONS_SAVED')
'''


async def ask(ctx,schema,system,prompt,timeout,tokens=2000):
    model=structured_model(cap_output(ctx.coder_llm if schema is CandidateCode else ctx.planner_llm,tokens),schema)
    result=await asyncio.wait_for(model.ainvoke(as_messages(system,prompt)),timeout)
    return result if isinstance(result,schema) else schema.model_validate(result)


async def compare(ctx,branch):
    store=root_store(ctx);db=ExperimentStore(store);order=ctx.experience_order
    tid=branch['subproblem']['id'];revision=order.revision if order else 1
    candidates=branch['design'].get('candidates',[])[:2];hashes=[digest(c) for c in candidates]
    scores=branch['design'].get('scores') or []
    record=db.read(tid,revision);policy=db.policy()
    intent=branch.get('experiment') or branch['design'].get('experiment') or {}
    if record:
        if not current(ctx,record,branch):
            record.update(status='input_changed',chosen_index=None,reason='输入、契约、候选或配置已改变，旧比较不晋升');db.save(record);return record
        if record['status'] in TERMINAL:return record
        for row in record['branches']:
            if row['status'] in {'generating','running'}:
                row.update(status='interrupted',error='上次操作中断，已预留预算不会退还，也不会重复执行')
    else:
        state=store.snapshot()
        files={k:v for k,v in order.input_refs.items() if not k.startswith(('task:','assumption:'))} if order else store.file_inputs()
        record=db.create(tid,revision,{'status':'preparing','owner_attempt':order.attempt_id if order else None,
            'candidate_hashes':hashes,'task_hash':digest(branch['subproblem']),'file_inputs':files,'input_refs':order.input_refs if order else files,
            'input_digest':order.input_digest if order else digest(store.file_inputs()),
            'contract_hash':digest(research_protocol(state,tid)) if state else digest(branch.get('research_contract',{})),
            'policy_version':None if intent.get('csv') else policy['version'],
            'intent_hash':digest(intent) if intent.get('csv') else None,
            'conditions':intent.get('conditions'),'evaluation':intent.get('evaluation'),
            'branches':[{'index':i,'name':c['name'],'instruction':c.get('idea',''),'candidate_hash':hashes[i],
                'prior':scores[i].get('total') if i<len(scores) else None,'estimator_contract':candidate_contract(c),
                'status':'pending','run_ids':[]} for i,c in enumerate(candidates)],'implementation_contract_version':CONTRACT_VERSION})
    if not current(ctx,record,branch):
        record.update(status='input_changed',chosen_index=None,reason='冻结的任务输入已改变，未执行比较');db.save(record);return record
    if len(candidates)!=2:
        for row in record['branches']:row.update(status='contract_error',error='双方案结构契约未满足，未执行候选')
        while len(record['branches'])<2:
            record['branches'].append({'index':len(record['branches']),'name':'候选未生成','status':'not_generated','run_ids':[],
                'error':'没有生成第二项可比较方案，不能用单方案自评替代'})
        record.update(status='no_valid',chosen_index=None,reason='双方案比较必须有两项不同候选',protocol_error=True);db.save(record);return record
    from ai_system.modules.methods import allowed_method_keys
    if any(not set(c.get('method_keys') or [])<=allowed_method_keys(ctx.skill_policy) for c in candidates):
        for row in record['branches']:row.update(status='contract_error',error='方案使用了当前不允许的方法')
        record.update(status='no_valid',chosen_index=None,reason='候选方法配置不匹配，需要重新设计');db.save(record);return record
    try:
        if 'protocol' not in record:
            if intent.get('csv'):spec=SupervisedSpec.model_validate(intent['csv'])
            elif policy.get('spec'):spec=SupervisedSpec.model_validate(policy['spec'])
            else:
                if not db.reserve(tid,'model_calls',20,record['id']+':protocol'):
                    record.update(status='budget_exhausted',reason='协议生成额度已消耗，未重置或重试',chosen_index=None);db.save(record);return record
                spec=await ask(ctx,SupervisedSpec,'为结构化监督学习选择共同协议。只用给定列类型；不适用设 applicable=false。指标限 MAE/RMSE/AUC，不能伪造列或凭结果改协议。',json.dumps({'question':branch['subproblem']['question'],'files':metadata(ctx.workspace)},ensure_ascii=False),20)
            if not spec.applicable:
                for row in record['branches']:row.update(status='not_applicable',error=spec.reason)
                record.update(status='not_applicable',reason=spec.reason,chosen_index=None);db.save(record);return record
            frozen,payload,truth,negative_truth,train,negative_train=prepare(ctx.workspace,spec.model_dump())
            if record['file_inputs'].get(frozen['dataset'])!=frozen['data_hash']:raise ValueError('执行副本与冻结数据哈希不匹配')
            record['protocol']=frozen;record['protocol_hash']=digest(frozen);record['status']='frozen';db.save(record)
        else:
            frozen,payload,truth,negative_truth,train,negative_train=prepare(ctx.workspace,record['protocol'])
            if digest(frozen)!=record['protocol_hash']:raise ValueError('数据或实际划分已改变')
    except Exception:
        for row in record['branches']:row.update(status='not_started',error='共同协议或数据条件未通过冻结检查，未执行候选')
        record.update(status='no_valid',chosen_index=None,reason='比较协议无法冻结或数据不适用；未执行候选、未选择胜者',protocol_error=True);db.save(record);return record
    directory=ctx.workspace/'outputs/intermediate/comparisons'/record['id'];directory.mkdir(parents=True,exist_ok=True)
    for row,candidate in zip(record['branches'],candidates):
        if row['status']!='pending':continue
        await ctx.events.stage('正在准备候选实验：'+candidate['name'])
        key=f"{record['id']}:code:{row['index']}"
        if not db.reserve(tid,'model_calls',25,key,candidate=True):row.update(status='budget_exhausted',error='候选生成预算不足');db.save(record);continue
        row['status']='generating';db.save(record);started=time.monotonic()
        answer=None
        try:
            prompt={'candidate':{'name':str(candidate.get('name',''))[:80],'method_keys':candidate.get('method_keys') or [],
                    'idea':str(candidate.get('idea',''))[:1500],'formulation':str(candidate.get('formulation',''))[:1500],
                    'steps':[str(step)[:400] for step in candidate.get('steps',[])[:8]],'variant':row['index']},
                'task_type':frozen['task_type'],'metric':frozen['metric'],'features':frozen['feature_dtypes'],'n_train':len(train),'seed':frozen['seed']}
            prompt['candidate']['estimator_contract']=row.get('estimator_contract')
            answer=await ask(ctx,CandidateCode,'只实现 JSON 中当前 candidate 的 sklearn 模块 make_estimator(seed)，严格遵守其方法、预处理和参数；不得自行改为另一候选或增加多项式/特征工程。对象使用 fit(X_train,y_train) 和 predict(X_validation)，二分类可用 predict_proba/decision_function。项目提供划分并计算指标；不要读取文件、访问验证标签、计算/报告分数或修改项目文件。只返回简短的工厂实现，不写长说明。',json.dumps(prompt,ensure_ascii=False),25)
            row.update(code=answer.code,code_hash=hashlib.sha256(answer.code.encode()).hexdigest(),ast_hash=valid_code(answer.code),status='code_frozen')
        except Exception as exc:
            row.update(status='code_error' if answer is not None else 'generation_error',error='候选代码不符合 make_estimator 契约' if answer is not None else '生成请求失败或超时，已耗预算且不自动重试')
            if type(exc).__name__ in {'AuthenticationError','PermissionDeniedError','RateLimitError'}:
                row.update(status='provider_error',error='网关拒绝授权或额度请求，停止后续比较调用，不自动重试')
                record['provider_unavailable']=True
                for remaining in record['branches']:
                    if remaining['status']=='pending':remaining.update(status='not_started',error='网关请求已被拒绝，未继续消耗请求')
        row['generation_seconds']=time.monotonic()-started;db.save(record)
        if record.get('provider_unavailable'):break
    ready=[r for r in record['branches'] if r['status']=='code_frozen']
    if record.get('provider_unavailable'):
        for row in ready:row.update(status='not_started',error='网关请求已被拒绝，未继续执行')
        record.update(status='no_valid',chosen_index=None,reason='网关授权或额度请求被拒绝，停止比较且不重试');db.save(record);return record
    if len(ready)==2 and ready[0]['ast_hash']==ready[1]['ast_hash']:
        for row in ready:row.update(status='contract_error',error='两候选代码结构相同，不能构成双方案比较')
        db.save(record)
    # Both candidate code blobs are frozen before any validation prediction exists.
    for row in record['branches']:
        if row['status']!='code_frozen':continue
        if not current(ctx,record,branch):record.update(status='input_changed',reason='执行前输入已改变',chosen_index=None);db.save(record);return record
        if not db.reserve(tid,'executions',25,f"{record['id']}:run:{row['index']}"):
            row.update(status='budget_exhausted',error='执行或超时额度不足');db.save(record);continue
        code=directory/f"candidate-{row['index']}.py";inputs=directory/f"inputs-{row['index']}.json";output=directory/f"predictions-{row['index']}.json"
        code.write_text(row['code']);inputs.write_text(json.dumps(payload,allow_nan=False));output.unlink(missing_ok=True)
        driver=DRIVER.format(payload=inputs.relative_to(ctx.workspace).as_posix(),code=code.relative_to(ctx.workspace).as_posix(),output=output.relative_to(ctx.workspace).as_posix(),guard=inspect.getsource(check_estimator),contract=row.get('estimator_contract'))
        row['driver_code']=driver;row['driver_hash']=hashlib.sha256(driver.encode()).hexdigest()
        key=f"{tid}~comparison{row['index']}";row['status']='running';db.save(record)
        kernel=ctx.kernels.get(key);kernel.timeout=25;started=time.monotonic()
        try:
            run=await kernel.execute(driver)
            row['run_ids'].append(run.run_id);row['stdout_hash']=hashlib.sha256(run.stdout.encode()).hexdigest()
            if not run.ok:
                row['error_detail']=diagnostic(run.error)
                raise RuntimeError('timeout' if run.timed_out else 'candidate execution error')
            if not output.is_file() or output.stat().st_size>2_000_000:raise ValueError('预测产物缺失或超预算')
            if hashlib.sha256(code.read_bytes()).hexdigest()!=row['code_hash']:raise ValueError('候选代码在执行期间被改动')
            predictions=json.loads(output.read_text());row['implementation_audit']=predictions['estimator_audit']
            measured=metric_value(frozen['metric'],truth,predictions['prediction'])
            negative=metric_value(frozen['metric'],negative_truth,predictions['negative_prediction'])
            base=baseline_value(frozen['metric'],train,truth);negative_base=baseline_value(frozen['metric'],negative_train,negative_truth)
            gain=measured-base if frozen['higher_is_better'] else base-measured
            negative_gain=negative-negative_base if frozen['higher_is_better'] else (negative_base-negative)/max(abs(negative_base),1e-12)
            row.update(metric=measured,baseline=base,improvement=gain,negative_metric=negative,negative_baseline=negative_base,
                status='control_inconclusive' if negative_gain>frozen['negative_control_tolerance'] else 'valid',
                predictions=[{'sample_id':sid,'observed':float(y),'prediction':float(v)} for sid,y,v in zip(frozen['validation_ids'],truth,predictions['prediction'])],
                prediction_hash=hashlib.sha256(output.read_bytes()).hexdigest(),kernel_key=key)
            if row['status']=='control_inconclusive':row['error']='置换对照异常，需复核泄漏或偶然性；不能直接判定方法无效'
        except Exception as exc:
            row.update(status='timeout' if 'timeout' in str(exc) else 'execution_error',error='候选运行超时' if 'timeout' in str(exc) else '候选执行或预测契约失败')
            row.setdefault('error_detail',diagnostic(exc))
        row['elapsed_seconds']=time.monotonic()-started;row['timeout_reserved']=25;db.save(record)
    status,index,reason=choose(record)
    if record.get('provider_unavailable'):status,index,reason='no_valid',None,'网关授权或额度请求被拒绝，停止比较且不重试'
    if any(r['status']=='budget_exhausted' for r in record['branches']):status,index,reason='budget_exhausted',None,'比较预算耗尽，未回退模型自评选择胜者'
    record.update(status=status,chosen_index=index,reason=reason)
    for row in record['branches']:
        row['selection_reason']=row.get('error') or reason if index is None else reason if row['index']==index else row.get('error') or '保留同一条件下的实测结果，不宣称该方法无效'
    if not current(ctx,record,branch):record.update(status='input_changed',chosen_index=None,reason='执行期间输入已改变，比较结果仅作历史，不晋升')
    db.save(record);return record
