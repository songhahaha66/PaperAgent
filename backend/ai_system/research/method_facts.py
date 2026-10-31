"""Read only, current project-verified estimator facts for writing and review.

Never infer an estimator from its display name or execute frozen model code here.
Unverified, stale and unsupported model families yield no correction authority.
"""
from services.file_services.workspace_layout import workspace_path

from .domain import experiment_design
import hashlib,json
from contextlib import closing
from .store import digest
from .validation import protocol


def verified_methods(store):
    if store is None:return []
    state=store.snapshot()
    if not state:return []
    with closing(store._connect()) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='experiment_comparisons'").fetchone():return []
        records=[json.loads(r[0]) for r in db.execute('SELECT payload FROM experiment_comparisons WHERE research_id=?',(store.research_id,))]
        policy=db.execute('SELECT version FROM experiment_policy WHERE id=1').fetchone()
    files=store.file_inputs();facts=[]
    try:manifest=json.loads((workspace_path(store.root, 'manifest.json')).read_text()).get('runs',{})
    except (OSError,ValueError):return []
    for row in records:
        task=state['experiments'].get(row['task_id'])
        if not task or task['retired'] or task['revision']!=row['revision'] or task['validity']!='current' or task['execution_status']!='succeeded':continue
        if row.get('implementation_contract_version')!=1 or row.get('file_inputs')!=files or not policy or row.get('policy_version')!=policy[0]:continue
        if row.get('contract_hash')!=digest(protocol(state,row['task_id'])) or row.get('task_hash')!=digest(task['subproblem']):continue
        attempt=state['executions'].get(task['execution_id'])
        if not attempt or row.get('input_refs')!=attempt['input_refs'] or store._inputs(state,task,files)!=attempt['input_refs']:continue
        candidates=(experiment_design(state, task) or {}).get('candidates',[])[:2]
        if row.get('candidate_hashes')!=[digest(c) for c in candidates]:continue
        for branch in row['branches']:
            audit=branch.get('implementation_audit') or {};contract=branch.get('estimator_contract') or {}
            if branch.get('status')!='valid' or not audit.get('checked') or audit.get('family') not in {'ols','ridge'}:continue
            if audit.get('family')!=contract.get('family') or type(audit.get('fit_intercept')) is not bool:continue
            if hashlib.sha256(branch.get('code','').encode()).hexdigest()!=branch.get('code_hash'):continue
            preprocess=audit.get('preprocessors',[])
            if any(p!='StandardScaler' for p in preprocess) or bool(preprocess)!=contract.get('standardize'):continue
            if contract.get('alpha') is not None and audit.get('alpha')!=contract['alpha']:continue
            expected='LinearRegression' if audit['family']=='ols' else 'Ridge'
            if not audit.get('estimator','').startswith('sklearn.') or not audit['estimator'].endswith('.'+expected):continue
            try:
                artifact=store.root/'experiments'/task['execution_id']/'workspace/outputs/intermediate/comparisons'/row['id']/f"predictions-{branch['index']}.json"
                if not artifact.resolve().is_relative_to(store.root.resolve()):continue
                raw=artifact.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=branch['prediction_hash'] or json.loads(raw)['estimator_audit']!=audit:continue
                run=manifest[branch['run_ids'][-1]]
                paths=[workspace_path(store.root,run[k]) for k in ('input','stdout')]
                if run['status']!='success' or not run.get('evidence_archived'):continue
                if any(hashlib.sha256(p.read_bytes()).hexdigest()!=h for p,h in zip(paths,[branch['driver_hash'],branch['stdout_hash']])):continue
            except (OSError,KeyError,ValueError,IndexError):continue
            facts.append({'task_id':row['task_id'],'revision':task['revision'],'attempt_id':task['execution_id'],
                'comparison_id':row['id'],'branch_index':branch['index'],'code_hash':branch['code_hash'],'protocol_hash':row['protocol_hash'],
                'family':audit['family'],'fit_intercept':audit['fit_intercept'],'standardize':bool(preprocess),'alpha':audit.get('alpha'),
                'estimator':audit['estimator'],'audit_artifact_sha256':branch['prediction_hash'],'run_id':branch['run_ids'][-1],
                'scope':'project_checked_estimator_on_current_frozen_split'})
    return facts


def method_context(store):
    facts=verified_methods(store)
    return ('\n\n# 当前实际核验的模型设置（公式须匹配实现，不能凭显示名称推断）\n'+json.dumps(facts,ensure_ascii=False)
        +'\nfit_intercept=true 表示截距单独拟合且不惩罚；使用增广设计矩阵时须明确常数列，或明确训练集中心化及截距恢复。'
        'standardize=true 表示训练集拟合的 StandardScaler，不能在验证集重新拟合。未记录的缩放参数和系数不得编造。') if facts else ''


def canonical_method_block(facts,task_id):
    """Protected explanation for the supported audited estimator families only."""
    rows=[f for f in facts if f['task_id']==task_id]
    if not rows or any(not f['fit_intercept'] for f in rows):raise ValueError('此任务缺少可用于修正公式的带截距估计器核验证据')
    parts=[r'方法公式与实际拟合设置一致：$X$ 为训练特征矩阵，$y$ 为训练响应，横线表示训练集均值，上标 $+$ 表示 Moore--Penrose 广义逆。下式中均值和预处理仅由训练集确定，截距单独估计且不施加正则惩罚。']
    for fact in rows:
        if fact['family']=='ols':
            prefix=(r'普通最小二乘先用训练集标准化变换得到 $u=T_{\mathrm{train}}(x)$，以变换后的特征构造 $U$。' if fact['standardize'] else r'普通最小二乘使用原始特征 $u=x$，记 $U=X$。')
            parts.append(prefix+r'记 $U_c=U-\mathbf{1}\bar{u}^{\mathsf T}$、$y_c=y-\bar{y}\mathbf{1}$，则'
                '\n'+r'\[\hat\beta=U_c^{+}y_c,\qquad \hat b=\bar y-\bar u^{\mathsf T}\hat\beta,\qquad \hat y(x)=\hat b+u^{\mathsf T}\hat\beta.\]')
        elif fact['family']=='ridge':
            prefix=(r'先用仅在训练集拟合的标准化变换得到 $z=T_{\mathrm{train}}(x)$，再以变换后的特征构造 $Z$。' if fact['standardize'] else '使用原始特征 $z=x$ 构造 $Z$。')
            parts.append(prefix+r'记 $Z_c=Z-\mathbf{1}\bar z^{\mathsf T}$，则'
                '\n'+r'\[\hat\beta_{\mathrm{ridge}}=(Z_c^{\mathsf T}Z_c+\alpha I)^{-1}Z_c^{\mathsf T}(y-\bar y\mathbf{1}),\qquad \hat b_{\mathrm{ridge}}=\bar y-\bar z^{\mathsf T}\hat\beta_{\mathrm{ridge}}.\]'
                '\n'+r'预测为 $\hat y(x)=\hat b_{\mathrm{ridge}}+z^{\mathsf T}\hat\beta_{\mathrm{ridge}}$，其中惩罚项仅作用于斜率系数。')
    return '\n\n'.join(parts)
