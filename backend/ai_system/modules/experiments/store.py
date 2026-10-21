from ai_system.research.domain import experiment_design
"""Frozen supervised comparison protocols and durable, shared exploration budgets."""
import json
import time
from contextlib import closing
from typing import Literal
from pydantic import BaseModel,Field,model_validator,ConfigDict
from ai_system.research.store import ResearchStore,digest
from ai_system.research.models import ResearchConflict

LIMITS={'model_calls':6,'executions':4,'candidates':4,'timeout_seconds':240}


class SupervisedSpec(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    applicable:bool=True
    reason:str=''
    dataset:str=Field(default='',max_length=300)
    target:str=Field(default='',max_length=100)
    features:list[str]=Field(default_factory=list,max_length=100)
    id_column:str|None=None
    task_type:Literal['regression','binary']='regression'
    metric:Literal['MAE','RMSE','AUC']='RMSE'
    split:Literal['random','group','time']='random'
    split_column:str|None=None
    validation_fraction:float=Field(default=.2,ge=.1,le=.4)
    seed:int=Field(default=42,ge=0,le=2**31-2)
    minimum_improvement:float=Field(default=0,ge=0)
    tie_tolerance:float=Field(default=1e-6,ge=0)
    tie_rule:Literal['candidate_order']='candidate_order'
    negative_control_tolerance:float=Field(default=.1,ge=0,le=.5)

    @model_validator(mode='after')
    def consistent(self):
        if self.applicable and (not self.dataset or not self.target or not self.features):raise ValueError('比较需明确 CSV、目标列与特征列')
        if self.task_type=='binary' and self.metric!='AUC' or self.task_type=='regression' and self.metric=='AUC':raise ValueError('指标与任务类型不匹配')
        if self.target in self.features or len(set(self.features))!=len(self.features):raise ValueError('特征不能含目标或重复列')
        if self.id_column in self.features or self.split_column in self.features:raise ValueError('样本 ID 与划分列不能直接作为特征')
        if self.split!='random' and not self.split_column:raise ValueError('分组/时间划分必须指定列')
        return self


class CandidateCode(BaseModel):
    code:str=Field(min_length=20,max_length=20000,description='完整 Python 模块，定义 make_estimator(seed)，返回有 fit/predict 的 sklearn 管线；不读取文件、不划分数据、不计算指标')


class ExperimentStore:
    def __init__(self,store):
        self.store=store
        with closing(store._connect()) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS experiment_policy (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS experiment_budgets (research_id TEXT NOT NULL,task_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(research_id,task_id));
                CREATE TABLE IF NOT EXISTS experiment_comparisons (id TEXT PRIMARY KEY,research_id TEXT NOT NULL,task_id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,UNIQUE(research_id,task_id,revision));''')

    def policy(self):
        with closing(self.store._connect()) as db:r=db.execute('SELECT version,payload FROM experiment_policy WHERE id=1').fetchone()
        return {'version':r[0],**json.loads(r[1])} if r else {'version':0,'enabled':False,'spec':None}

    def set_policy(self,enabled,spec=None):
        spec=SupervisedSpec.model_validate(spec).model_dump() if spec else None
        with self.store._transaction() as db:
            old=db.execute('SELECT version FROM experiment_policy WHERE id=1').fetchone();version=(old[0] if old else 0)+1
            db.execute('INSERT OR REPLACE INTO experiment_policy VALUES (1,?,?)',(version,json.dumps({'enabled':enabled,'spec':spec})))
        return self.policy()

    def budget(self,task_id,db=None):
        if db is None:
            with closing(self.store._connect()) as conn:return self.budget(task_id,conn)
        row=db.execute('SELECT payload FROM experiment_budgets WHERE research_id=? AND task_id=?',(self.store.research_id,task_id)).fetchone()
        return json.loads(row[0]) if row else {'limits':dict(LIMITS),'used':dict.fromkeys(LIMITS,0),'reservations':[]}

    def reserve(self,task_id,kind,seconds,key,candidate=False):
        with self.store._transaction() as db:
            budget=self.budget(task_id,db)
            if any(r['key']==key for r in budget['reservations']):return False
            amounts={kind:1,'timeout_seconds':seconds}
            if candidate:amounts['candidates']=1
            if any(budget['used'][k]+v>budget['limits'][k] for k,v in amounts.items()):return False
            for k,v in amounts.items():budget['used'][k]+=v
            budget['reservations'].append({'key':key,'kind':kind,'seconds':seconds,'at':time.time()})
            db.execute('INSERT OR REPLACE INTO experiment_budgets VALUES (?,?,?)',(self.store.research_id,task_id,json.dumps(budget)))
        return True

    def read(self,task_id,revision):
        with closing(self.store._connect()) as db:
            r=db.execute('SELECT payload FROM experiment_comparisons WHERE research_id=? AND task_id=? AND revision=?',(self.store.research_id,task_id,revision)).fetchone()
        return json.loads(r[0]) if r else None

    def accepted_summaries(self,snapshot):
        """Project-measured comparisons bound to the active accepted handoff.

        This is a read projection of existing records, not another acceptance
        verdict. Scores assigned by a model and candidate stdout are excluded.
        """
        import math
        from types import SimpleNamespace
        from ai_system.research.models import WorkOrder
        summaries=[]
        for key,task in snapshot['experiments'].items():
            if task['retired'] or task['kind']=='verify' or task['acceptance']!='accepted' or task['validity']!='current':
                continue
            attempt=snapshot['executions'].get(task['execution_id']) or {}
            # Continuations preserve the original measured comparison receipt;
            # never refit candidates to recreate its summary.
            while attempt.get('parent_attempt'):
                attempt=snapshot['executions'].get(attempt['parent_attempt']) or {}
            view_task={**task,'execution_id':attempt.get('attempt_id')}
            if attempt.get('execution_status')!='succeeded' or attempt.get('acceptance')!='accepted' or not attempt.get('commit',{}).get('published'):
                continue
            record=self.read(key,task['revision'])
            if not record or record.get('status') not in {'winner','only_valid','tied','no_valid','budget_exhausted'} or record.get('protocol_error'):
                continue
            try:
                from .supervised import current,choose
                from .contracts import VERSION,candidate_contract
                order=WorkOrder.model_validate(attempt['result']['order'])
                if order.attempt_id!=attempt['attempt_id'] or order.task_id!=key or order.revision!=task['revision']:
                    continue
                ctx=SimpleNamespace(research=self.store,experience_store=None,experience_order=order)
                design=experiment_design(snapshot,view_task) or {};candidates=design.get('candidates',[])[:2]
                if record.get('research_id')!=self.store.research_id or record.get('task_id')!=key or not current(ctx,record,{'subproblem':task['subproblem'],'design':design},allow_inherited=True):
                    continue
                if record.get('measurements'):
                    summary=self.measured_summary(snapshot,view_task,attempt,record)
                    if summary:summaries.append(summary)
                    continue
                protocol=record['protocol'];rows=record['branches'];chosen=record['chosen_index']
                if record.get('implementation_contract_version')!=VERSION or digest(protocol)!=record['protocol_hash']:
                    continue
                if len(rows)!=2 or len(candidates)!=2 or {r['index'] for r in rows}!={0,1}:
                    continue
                by_index={r['index']:r for r in rows};winner=by_index[chosen]
                if any(r.get('candidate_hash')!=record['candidate_hashes'][r['index']] or
                       r.get('estimator_contract')!=candidate_contract(candidates[r['index']]) for r in rows):
                    continue
                if any(r['status']=='valid' and (not r.get('run_ids') or not r.get('prediction_hash') or not r.get('code_hash') or
                       not math.isfinite(r['metric']) or not math.isfinite(r['baseline']) or
                       r.get('estimator_contract') and not r.get('implementation_audit',{}).get('checked')) for r in rows):
                    continue
                outcome,index,_=choose(record)
                if (outcome,index)!=(record['status'],chosen):
                    continue
                handoff=None
                expected={'comparison_'+protocol['metric'].lower():winner['metric'],
                          'comparison_baseline_'+protocol['metric'].lower():winner['baseline'],
                          'comparison_train_count':len(protocol['train_ids']),
                          'comparison_validation_count':len(protocol['validation_ids']),
                          'comparison_total_count':len(protocol['train_ids'])+len(protocol['validation_ids'])}
                for proof in record.get('formal_handoffs',[]):
                    refs=proof.get('evidence_refs') or []
                    if not refs or proof.get('protocol_hash')!=record['protocol_hash'] or proof.get('code_hash')!=winner['code_hash'] or not proof.get('artifact_hash'):
                        continue
                    evidence=[snapshot['artifacts'].get(ref,{}) for ref in refs]
                    if not all(e.get('active') and e.get('producer_attempt')==order.attempt_id and
                               e.get('payload',{}).get('run_id')==proof.get('run_id') for e in evidence):
                        continue
                    values={e['payload']['name']:float(e['payload']['value']) for e in evidence}
                    if all(name in values and math.isclose(values[name],value,rel_tol=1e-9,abs_tol=1e-10) for name,value in expected.items()):
                        handoff=proof;break
                if handoff is None:
                    continue
                boundary={k:protocol.get(k) for k in ('dataset','data_hash','target','features','task_type','metric','higher_is_better',
                    'split','split_column','id_column','seed','validation_fraction','positive_label','baseline','baseline_value',
                    'minimum_improvement','tie_tolerance','tie_rule','negative_control','negative_control_tolerance',
                    'negative_baseline_value','feature_dtypes')}
                boundary.update(train_count=len(protocol['train_ids']),validation_count=len(protocol['validation_ids']),
                                split_hash=digest([protocol['train_ids'],protocol['validation_ids']]))
                branches=[]
                for r in sorted(rows,key=lambda r:r['index']):
                    branches.append({**{k:r.get(k) for k in ('index','status','metric','baseline','improvement','negative_metric',
                        'negative_baseline','run_ids','candidate_hash','code_hash','prediction_hash','estimator_contract',
                        'implementation_audit','selection_reason','error')},
                        'name':candidates[r['index']]['name'],'method_keys':candidates[r['index']].get('method_keys',[]),
                        'eligible':r['status']=='valid' and r.get('improvement',float('-inf'))>=protocol['minimum_improvement']})
                summaries.append({'id':record['id'],'task_id':key,'attempt_id':order.attempt_id,'revision':task['revision'],
                    'status':record['status'],'chosen_index':chosen,'reason':record['reason'],'protocol':boundary,'branches':branches,
                    'provenance':{'table':'experiment_comparisons','record_id':record['id'],'owner_attempt':record.get('owner_attempt'),
                        'protocol_hash':record['protocol_hash'],'contract_hash':record['contract_hash'],'policy_version':record['policy_version'],
                        'formal_run_id':handoff['run_id'],'formal_artifact_hash':handoff['artifact_hash'],
                        'formal_evidence_refs':handoff['evidence_refs']}})
            except (KeyError,ValueError,TypeError,IndexError,OSError,StopIteration):
                # Incomplete/old records remain inspectable in ExperimentStore;
                # they cannot become current completed evidence by inference.
                continue
        return summaries

    def measured_summary(self,snapshot,task,attempt,record):
        """Read the existing comparison and published evidence, including ties.

        Candidate failures stay observations. Only the accepted measurement
        receipt can expose a comparison as evidence to the controller.
        """
        import hashlib,math
        from .contracts import candidate_contract,VERSION
        from .supervised import choose
        if record.get('implementation_contract_version')!=VERSION or digest(record['protocol'])!=record['protocol_hash']:
            return None
        proof=next((p for p in reversed(record['measurements']) if p.get('attempt_id')==attempt['attempt_id']),None)
        if not proof or proof['protocol_hash']!=record['protocol_hash']:return None
        if choose(record)[:2]!=(record['status'],record.get('chosen_index')):return None
        evidence=[snapshot['artifacts'].get(ref,{}) for ref in proof['evidence_refs']]
        if not all(e.get('active') and e.get('producer_attempt')==attempt['attempt_id'] and
                   e.get('payload',{}).get('run_id')==proof['run_id'] for e in evidence):return None
        actual={e['payload']['name']:e['payload']['value'] for e in evidence if e['payload']['kind']!='figure'}
        if any(name not in actual or not math.isclose(float(actual[name]),value,rel_tol=1e-9,abs_tol=1e-10)
               for name,value in proof['values'].items()):return None
        folder=self.store.root/'.system/attempts'/record['owner_attempt']/'workspace/outputs/intermediate/comparisons'/record['id']
        for measured in proof['measurements']:
            row=next(r for r in record['branches'] if r['index']==measured['index'])
            candidate=experiment_design(snapshot,task)['candidates'][row['index']]
            if row.get('candidate_hash')!=digest(candidate) or row.get('estimator_contract')!=candidate_contract(candidate):return None
            if row.get('estimator_contract') and not row.get('implementation_audit',{}).get('checked'):return None
            if not row.get('code_hash') or hashlib.sha256(row['code'].encode()).hexdigest()!=row['code_hash']:return None
            if hashlib.sha256((folder/f"predictions-{row['index']}.json").read_bytes()).hexdigest()!=measured['prediction_hash']:return None
            if row.get('prediction_hash')!=measured['prediction_hash'] or not math.isclose(row['metric'],measured['metric'],rel_tol=1e-9,abs_tol=1e-10):return None
        return {'id':record['id'],'task_id':task['task_id'],'revision':task['revision'],'status':record['status'],
            'chosen_index':record.get('chosen_index'),'reason':record['reason'],
            'conditions':task['experiment']['conditions'],'evaluation':task['experiment']['evaluation'],
            'protocol':{k:v for k,v in record['protocol'].items() if k not in {'train_ids','validation_ids'}},
            'branches':[{k:r.get(k) for k in ('name','status','metric','baseline','improvement','error','run_ids','code_hash','prediction_hash')} for r in record['branches']],
            'provenance':{'record_id':record['id'],'protocol_hash':record['protocol_hash'],
                          'formal_run_id':proof['run_id'],'formal_evidence_refs':proof['evidence_refs']}}

    def create(self,task_id,revision,record):
        with self.store._transaction() as db:
            old=db.execute('SELECT payload FROM experiment_comparisons WHERE research_id=? AND task_id=? AND revision=?',(self.store.research_id,task_id,revision)).fetchone()
            if old:return json.loads(old[0])
            record={**record,'id':'P'+digest([self.store.research_id,task_id,revision])[:24],
                    'task_id':task_id,'revision':revision,'research_id':self.store.research_id,'created_at':time.time()}
            db.execute('INSERT INTO experiment_comparisons VALUES (?,?,?,?,?)',(record['id'],self.store.research_id,task_id,revision,json.dumps(record)))
        return record

    def save(self,record):
        with self.store._transaction() as db:
            row=db.execute('SELECT payload FROM experiment_comparisons WHERE id=?',(record['id'],)).fetchone()
            if not row:raise ResearchConflict('比较记录不存在')
            old=json.loads(row[0])
            for key in ('protocol','protocol_hash','candidate_hashes','task_hash','input_refs','file_inputs','input_digest','contract_hash','policy_version','revision','implementation_contract_version','intent_hash','conditions','evaluation'):
                if key in old and old[key]!=record.get(key):raise ResearchConflict('比较协议或候选已冻结，不能修改')
            for before,after in zip(old['branches'],record['branches']):
                if any(before.get(k)!=after.get(k) for k in ('name','instruction')):
                    raise ResearchConflict('候选方法声明已冻结')
                if 'estimator_contract' in before and before['estimator_contract']!=after.get('estimator_contract'):
                    raise ResearchConflict('候选方法契约已经冻结')
                if before.get('code_hash') and any(before.get(k)!=after.get(k) for k in ('code','code_hash','ast_hash')):
                    raise ResearchConflict('候选代码已经冻结')
                if before.get('driver_hash') and any(before.get(k)!=after.get(k) for k in ('driver_code','driver_hash')):
                    raise ResearchConflict('项目执行代码已经冻结')
            db.execute('UPDATE experiment_comparisons SET payload=? WHERE id=?',(json.dumps(record,ensure_ascii=False,allow_nan=False),record['id']))

    def list(self):
        with closing(self.store._connect()) as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM experiment_comparisons ORDER BY rowid DESC LIMIT 30')]
        for r in rows:r['budget']=self.budget_for(r['research_id'],r['task_id'])
        return rows

    def budget_for(self,research_id,task_id):
        probe=ExperimentStore.__new__(ExperimentStore);probe.store=ResearchStore(self.store.root,research_id,read_only=True)
        return probe.budget(task_id)
