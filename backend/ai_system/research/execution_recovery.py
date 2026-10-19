"""Audited, explicit recovery of a frozen OLS/Ridge refit refusal.

This narrow policy does not resolve arbitrary execution faults. Legacy tool
refusals are reconstructed from the saved transcript (and labelled as such),
not invented as failed kernel runs. No LLM or candidate execution is performed.
"""

from ai_system.research.domain import findings as research_findings
import ast
import hashlib
import json
import re
import time
from pathlib import Path
import numpy as np
from .store import digest
from .validation import protocol,evaluate
from ai_system.modules.experiments.store import ExperimentStore

REFUSAL='本任务方案已冻结；禁止重新 fit/调参。请复用 comparison_estimator、_formal_truth、_formal_pred 和已登记的 comparison 指标。'
POLICY='frozen-linear-refit-recovery-v1'


def _read(root,path):
    target=(root/path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():raise ValueError('恢复证据文件缺失或越界')
    data=target.read_bytes()
    if len(data)>8_000_000:raise ValueError('恢复证据超过读取上限')
    return data


def _hash(data):return hashlib.sha256(data).hexdigest()


def _numbers(stdout):
    rows={}
    for line in stdout.splitlines():
        match=re.fullmatch(r'([a-zA-Z_]\w*)\s+([-+\d.eE]+)',line.strip())
        if match:
            value=float(match[2])
            if np.isfinite(value):rows[match[1]]=value
    return rows


def _lineage(code):
    """Check printed scalars derive from the frozen arrays and actual solves."""
    tree=ast.parse(code);labels={};taints={'_data':{'train'},'_formal_train':{'train'},'_V':{'validation'},
        '_formal_truth':{'truth'},'_formal_pred':{'frozen_prediction'}}
    def inputs(node):
        found=set().union(*(taints.get(n.id,set()) for n in ast.walk(node) if isinstance(n,ast.Name)))
        for n in ast.walk(node):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute):
                if n.func.attr=='lstsq':found.add('ols')
                if n.func.attr=='solve':found.add('ridge')
        return found
    for statement in tree.body:
        if isinstance(statement,(ast.Assign,ast.AnnAssign)):
            value=statement.value
            for target in statement.targets if isinstance(statement,ast.Assign) else [statement.target]:
                for name in ast.walk(target):
                    if isinstance(name,ast.Name):taints[name.id]=inputs(value)
        if isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Call):
            call=statement.value
            if isinstance(call.func,ast.Name) and call.func.id=='print' and len(call.args)==2 and isinstance(call.args[0],ast.Constant) and isinstance(call.args[0].value,str):
                labels[call.args[0].value]=inputs(call.args[1])
    return labels


def preview(store,issue_id,*,refusal_id=None,computation_run=None):
    source_artifacts={}
    def read(path):
        data=_read(store.root,path)
        if path!='manifest.json':source_artifacts[path]=_hash(data)
        return data
    state=store.snapshot();issue=next((i for i in research_findings(state) if i['id']==issue_id),None)
    if not issue:raise ValueError('执行问题不存在')
    if issue.get('resolution',{}).get('policy')==POLICY and issue['status']=='resolved':return issue['resolution']
    if issue['category']!='execution' or issue['source']!='worker' or issue['status']!='confirmed' or issue['action']!='retry' or issue.get('assumption_id'):
        raise ValueError('该问题不属于本恢复策略可关闭的工具拒绝')
    task=state['tasks'][issue['task_id']];aid=issue['producer_attempt'];attempt=state['attempts'][aid]
    if task['active_attempt']!=aid or task['revision']!=issue['task_revision'] or attempt['revision']!=task['revision'] or task['validity']!='current':raise ValueError('任务版本或当前尝试已改变，不能沿用恢复证据')
    refs=store._inputs(state,task,store.file_inputs())
    if refs!=attempt['input_refs'] or digest(protocol(state,issue['task_id']))!=attempt['protocol_hash']:raise ValueError('输入或冻结验收契约已改变，不能关闭')
    validation=evaluate(state,issue['task_id'],[]).model_dump()
    if validation!=attempt['validation'] or validation['outcome']!='accepted' or not validation['checks'] or any(c['outcome']!='passed' for c in validation['checks']):raise ValueError('后续结果没有通过同一契约的全部检查')
    from .experience import grounded
    checked_refs=sorted({rid for c in validation['checks'] for rid in c['evidence_refs']}|set(issue['evidence_refs']))
    provenance=grounded(store.root,state,checked_refs)
    if len(provenance)!=len(checked_refs):raise ValueError('当前检查或问题证据缺少成功执行来源')
    if task['acceptance']!='accepted' or attempt['execution_status']!='succeeded':raise ValueError('没有成功的当前结果')
    workspace=store.root/'.system/attempts'/aid/'workspace'
    transcript_path=f'.system/attempts/{aid}/workspace/.system/transcripts/{issue["task_id"]}.json'
    raw=read(transcript_path);messages=json.loads(raw);calls={};refusals=[];runs=[]
    manifest=json.loads(read('manifest.json'))['runs']
    for index,message in enumerate(messages):
        data=message['data']
        if message['type']=='ai':
            for call in data.get('tool_calls',[]):
                if call['name']=='run_python':calls[call['id']]={'code':call['args']['code'],'index':index}
        elif message['type']=='tool' and data.get('tool_call_id') in calls:
            call=calls[data['tool_call_id']];content=str(data.get('content',''))
            if content==REFUSAL:refusals.append({**call,'tool_call_id':data['tool_call_id'],'response_index':index,'content_hash':digest(content)})
            found=re.match(r'\[run_id: ([A-Za-z0-9_]+)\]',content)
            if found:
                rid=found[1];info=manifest.get(rid,{})
                if info.get('status')!='success':
                    raise ValueError('同一尝试还有失败内核运行；本策略不能确认完整恢复')
                code=read(info['input']);stdout=read(info['stdout'])
                if code.decode()!=call['code']:raise ValueError('工具调用代码与已保存成功 run 不一致')
                runs.append({**call,'run_id':rid,'tool_call_id':data['tool_call_id'],'code_hash':_hash(code),'stdout_hash':_hash(stdout),'numbers':_numbers(stdout.decode()),'lineage':_lineage(code.decode())})
    selected=[r for r in refusals if refusal_id is None or r['tool_call_id']==refusal_id]
    if len(selected)!=1:raise ValueError('工具拒绝关联有歧义或缺失，必须选择唯一原始事件')
    refused=selected[0]
    comparison_store=ExperimentStore(store)
    record=comparison_store.read(issue['task_id'],task['revision'])
    if not record or record.get('owner_attempt')!=aid or record.get('implementation_contract_version')!=1 or record['status']!='winner' or record['contract_hash']!=digest(protocol(state,issue['task_id'])) or record['file_inputs']!=store.file_inputs():raise ValueError('缺少同一尝试、输入和验收契约的已核查冻结比较')
    if record.get('policy_version')!=comparison_store.policy()['version'] or record.get('candidate_hashes')!=[digest(c) for c in (task.get('design') or {}).get('candidates',[])[:2]] or record.get('task_hash')!=digest(task['subproblem']) or record.get('input_refs')!=attempt['input_refs']:
        raise ValueError('比较配置、任务或冻结候选已改变')
    from ai_system.modules.experiments.supervised import prepare,metric_value
    frozen,payload,truth,_,_,_=prepare(store.root,record['protocol'])
    if digest(frozen)!=record['protocol_hash'] or frozen['metric']!='RMSE' or frozen['task_type']!='regression':raise ValueError('冻结比较协议已改变或不适用')
    # The refused operation must actually be the declared OLS/Ridge experiment.
    tree=ast.parse(refused['code']);constructors={n.func.id:n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
    if not {'LinearRegression','StandardScaler','Ridge'}<=set(constructors) or 'PolynomialFeatures' in constructors or not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='fit' for n in ast.walk(tree)):raise ValueError('被拒绝操作不是声明的双线性模型重新拟合')
    alpha=next((k.value.value for k in constructors['Ridge'].keywords if k.arg=='alpha' and isinstance(k.value,ast.Constant)),None)
    branches={b.get('implementation_audit',{}).get('family'):b for b in record['branches']}
    if set(branches)!={'ols','ridge'} or alpha!=branches['ridge'].get('implementation_audit',{}).get('alpha'):raise ValueError('被拒绝操作和冻结方法/参数不匹配')
    folder=workspace/'outputs/intermediate/comparisons'/record['id'];artifacts={}
    for family,branch in branches.items():
        if branch['status']!='valid' or not branch['implementation_audit']['checked']:raise ValueError('冻结方法未通过项目实际估计器核查')
        pred_path=(folder/f'predictions-{branch["index"]}.json').relative_to(store.root).as_posix();pred_raw=read(pred_path)
        input_path=(folder/f'inputs-{branch["index"]}.json').relative_to(store.root).as_posix()
        if json.loads(read(input_path))!=payload:raise ValueError('比较的实际执行输入与冻结数据不一致')
        if _hash(pred_raw)!=branch['prediction_hash']:raise ValueError('冻结预测产物已改变')
        pred=json.loads(pred_raw)['prediction'];reference=[p['prediction'] for p in branch['predictions']]
        if not np.allclose(pred,reference,rtol=1e-10,atol=1e-12) or not np.isclose(metric_value('RMSE',truth,pred),branch['metric'],rtol=1e-10,atol=1e-12):raise ValueError('项目复算与冻结指标不一致')
        rid=branch['run_ids'][-1];info=manifest.get(rid,{})
        if info.get('status')!='success' or _hash(read(info['input']))!=branch['driver_hash']:raise ValueError('冻结比较缺少实际成功的项目执行来源')
        artifacts[family]={'comparison_run_id':rid,'prediction_hash':branch['prediction_hash'],'code_hash':branch['code_hash'],'metric':branch['metric']}
    # A later successful computation must cover both families, with actual
    # least-squares/solve dataflow; unrelated successes and literal prints fail.
    candidates=[]
    for run in runs:
        if run['index']<=refused['response_index'] or computation_run and run['run_id']!=computation_run:continue
        bindings={}
        for family in ('ols','ridge'):
            labels=[label for label,tags in run['lineage'].items() if {family,'train','validation','truth'}<=tags and label in run['numbers'] and np.isclose(run['numbers'][label],branches[family]['metric'],rtol=1e-9,atol=1e-12)]
            if len(labels)!=1:break
            bindings[family]=labels[0]
        if len(bindings)!=2:continue
        deltas=[label for label,tags in run['lineage'].items() if {'ols','frozen_prediction'}<=tags and label in run['numbers'] and 0<=run['numbers'][label]<=1e-9]
        if not deltas:continue
        candidates.append((run,bindings,deltas))
    if len(candidates)!=1:raise ValueError('没有唯一的后续成功复核覆盖该拒绝；保留执行问题')
    success,bindings,deltas=candidates[0]
    evidence={}
    for rid in issue['evidence_refs']:
        entry=state['evidence'].get(rid,{})
        if not entry.get('active') or entry.get('producer_attempt')!=aid:raise ValueError('问题证据不属于当前尝试')
        item=entry['payload'];matching=[family for family in branches if np.isclose(float(item['value']),branches[family]['metric'],rtol=1e-9,atol=1e-12)]
        source=next((r for r in runs if r['run_id']==item['run_id'] and r['index']>success['index']),None)
        if len(matching)!=1 or source is None or not any(np.isclose(value,float(item['value']),rtol=1e-9,atol=1e-12) for value in source['numbers'].values()):raise ValueError('问题引用没有关联到成功复核后的实际登记来源')
        evidence[matching[0]]={'ref':rid,'run_id':item['run_id'],'value':item['value'],'content_hash':entry['content_hash']}
    if set(evidence)!=set(branches):raise ValueError('问题证据没有覆盖所报告的两种方法')
    proof={'policy':POLICY,'research_id':store.research_id,'issue_id':issue_id,'original_issue_hash':digest({k:v for k,v in issue.items() if k not in {'resolution','resolution_attempt'}}),
        'task_id':issue['task_id'],'revision':task['revision'],'attempt_id':aid,'input_digest':attempt['input_digest'],'input_refs':refs,'protocol_hash':attempt['protocol_hash'],
        'comparison_id':record['id'],'comparison_protocol_hash':record['protocol_hash'],'experiment_policy_version':record['policy_version'],
        'candidate_hashes':record['candidate_hashes'],'task_hash':record['task_hash'],'transcript_path':transcript_path,'transcript_hash':_hash(raw),
        'refusal':{'source':'legacy_saved_transcript','tool_call_id':refused['tool_call_id'],'message_index':refused['response_index'],'code_hash':digest(refused['code']),'response_hash':refused['content_hash'],'kernel_executed':False},
        'successful_computation':{'run_id':success['run_id'],'tool_call_id':success['tool_call_id'],'code_hash':success['code_hash'],'stdout_hash':success['stdout_hash'],'metric_outputs':bindings,'prediction_delta_outputs':deltas},
        'registered_evidence':evidence,'frozen_artifacts':artifacts,'validation':validation,'check_provenance':provenance,
        'source_artifacts':source_artifacts,'result_digest':attempt['result_digest'],
        'manifest_runs':{rid:manifest[rid] for rid in {success['run_id'],*[b['run_ids'][-1] for b in branches.values()],*[e['run_id'] for e in evidence.values()],*[p['run_id'] for p in provenance]}}}
    proof['proof_hash']=digest(proof)
    return proof


def resolve(store,issue_id,proof_hash,*,actor_id=None):
    proof=preview(store,issue_id)
    if proof['proof_hash']!=proof_hash:raise ValueError('恢复证明已变化，请重新核对')
    with store._transaction() as db:
        state=store._read(db);issue=next(i for i in research_findings(state) if i['id']==issue_id)
        if issue['status']=='resolved':
            if issue.get('resolution',{}).get('proof_hash')==proof_hash:return issue['resolution']
            raise ValueError('问题已由其他证明关闭')
        if digest({k:v for k,v in issue.items() if k not in {'resolution','resolution_attempt'}})!=proof['original_issue_hash'] or store._inputs(state,state['tasks'][issue['task_id']],store.file_inputs())!=proof['input_refs'] or digest(protocol(state,issue['task_id']))!=proof['protocol_hash']:raise ValueError('关闭前问题、输入或契约已变化')
        task=state['tasks'][issue['task_id']]
        if task['active_attempt']!=proof['attempt_id'] or task['revision']!=proof['revision'] or task['validity']!='current' or state['attempts'][proof['attempt_id']]['result_digest']!=proof['result_digest']:
            raise ValueError('关闭前当前尝试或结果已改变')
        policy_version=db.execute('SELECT version FROM experiment_policy WHERE id=1').fetchone()
        if not policy_version or policy_version[0]!=proof['experiment_policy_version'] or digest(task['subproblem'])!=proof['task_hash'] or [digest(c) for c in (task.get('design') or {}).get('candidates',[])[:2]]!=proof['candidate_hashes']:
            raise ValueError('关闭前比较配置、任务或候选已改变')
        for path,expected in proof['source_artifacts'].items():
            if _hash(_read(store.root,path))!=expected:raise ValueError('关闭前执行证据文件已改变')
        current_manifest=json.loads(_read(store.root,'manifest.json'))['runs']
        if any(current_manifest.get(rid)!=entry for rid,entry in proof['manifest_runs'].items()):raise ValueError('关闭前执行来源记录已改变')
        issue.update(status='resolved',resolution_attempt=proof['attempt_id'],resolution=proof)
        store._save(db,state,'execution_issue_resolved',{'issue_id':issue_id,'previous_status':'confirmed',
            'recorded_at':time.time(),'actor_user_id':actor_id,'resolution':proof})
    return proof
