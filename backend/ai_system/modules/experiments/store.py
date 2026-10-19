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
            for key in ('protocol','protocol_hash','candidate_hashes','task_hash','input_refs','file_inputs','input_digest','contract_hash','policy_version','revision','implementation_contract_version'):
                if key in old and old[key]!=record.get(key):raise ResearchConflict('比较协议或候选已冻结，不能修改')
            for before,after in zip(old['branches'],record['branches']):
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
