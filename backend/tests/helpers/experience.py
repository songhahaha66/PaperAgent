"""Shared fixtures for experience and research setup tests."""
from ai_system.sandbox.evidence import seal_run
from services.file_services.workspace_layout import workspace_path

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.models import Base, User, Project
from services.research_experience import ExperienceScope
from ai_system.research.store import ResearchStore
from ai_system.research.models import WorkerResult

TASK='连续线性规划：最大化 3x+2y，约束 x+y<=4,x<=2,y<=3,x,y为非负连续实数；独立复核答案 x=2,z=10。'
DESIGN={'methods':['linear_programming'],'chosen':{'name':'线性规划','method_keys':['linear_programming'],
    'idea':'实际求解并检查','formulation':'linear constraints','steps':['读取当前问题','SciPy linprog 求解','代回原始约束和目标'],
    'validation':'核对本研究登记的 x 与 z'}}


def analysis(checks=True):
    return {'subproblems':[{'id':'Q1','title':'连续线性规划','question':TASK,'deliverables':['求解并复核'],'depends_on':[]}],
        'requirements':[{'id':'RQ1','statement':'复核答案','source_quote':'x=2,z=10','kind':'constraint' if checks else 'goal','task_ids':['Q1'],
          'checks':[{'id':'x_check','description':'复核最优产量','left':{'task_id':'Q1','name':'x'},'operator':'eq','expected':2},
                    {'id':'z_check','description':'复核目标值','left':{'task_id':'Q1','name':'z'},'operator':'eq','expected':10}] if checks else []}],
        'assumptions':[],'overall_analysis':'独立求解和验收'}


@pytest.fixture
def env(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'app.sqlite'),connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine);sessions=sessionmaker(bind=engine)
    with sessions() as db:
        for uid in (1,2):db.add(User(id=uid,username=f'user{uid}',email=f'{uid}@example.test',password_hash='unused'))
        for wid,uid in [('A',1),('B',1),('C',2)]:db.add(Project(work_id=wid,title=wid,created_by=uid))
        db.commit()
    def root(wid):return tmp_path/'workspaces'/wid
    def scope(wid='A',uid=1):return ExperienceScope(uid,wid,sessions,root)
    def store(wid='A',checks=True):
        s=ResearchStore(root(wid),'study'+wid,experience_scope=scope(wid,2 if wid=='C' else 1))
        s.initialize(TASK,analysis(checks));return s
    return SimpleNamespace(sessions=sessions,root=root,scope=scope,store=store,engine=engine)


def submit(store,x=2,z=10,order=None,*,execution='succeeded',error='',ground=True):
    order=order or store.claim('Q1')
    from services.file_services.workspace_fs import WorkspaceFS
    fs=WorkspaceFS(str(store.root));fs.init_structure();run_id=fs.create_run()
    if ground:
        fs.save_run_stdout(run_id,f'{x} {z}');fs.finish_run(run_id,'success'); seal_run(fs, run_id)
    rows=[{'id':store.allocate_ref('R'),'name':k,'value':str(v),'subproblem':'Q1','kind':'number','run_id':run_id} for k,v in [('x',x),('z',z)]] if execution=='succeeded' else []
    result=WorkerResult(order=order,execution_status=execution,acceptance='accepted' if rows else 'inconclusive',
        design=DESIGN,results=rows,report={'status':'completed' if rows else 'failed','error':error,
            'requirement_evidence':[{'requirement_id':'RQ1','evidence_refs':[r['id'] for r in rows]}] if rows else []})
    # Import a historical contract-verified experience fixture. New research no
    # longer creates validation records or promotes them automatically.
    from unittest.mock import patch
    scope=store.experience_scope
    with patch.object(scope,'index',return_value=0):
        receipt=store.commit(result)
    if execution=='succeeded':
        from ai_system.research.experience import grounded
        with store._transaction() as db:
            state=store._read(db);attempt=state['executions'][order.attempt_id]
            checks=[]
            for rule in attempt['experience_basis']['protocol']:
                check=rule['check'];row=next(r for r in rows if r['name']==check['left']['name'])
                checks.append({'check_id':check['id'],'owner_kind':rule['owner_kind'],'owner_id':rule['owner_id'],
                    'owner_revision':rule['owner_revision'],'evidence_refs':[row['id']],
                    'outcome':'passed' if float(row['value'])==check['expected'] else 'failed','message':'历史独立复核'})
            attempt['validation']={'protocol_hash':order.protocol_hash,'checks':checks,
                'outcome':'accepted' if all(c['outcome']=='passed' for c in checks) else 'rejected'}
            attempt['experience_evidence']=grounded(store.root,state,[ref for c in checks for ref in c['evidence_refs']])
            store._save(db,state,'import_historical_experience',{})
    scope.index(store)
    return receipt,result


def adopt_ready(scope):
    for card in scope.list()['cards']:
        if card['card']['level'] in {'verified','counterexample'} and card['status']!='adopted':
            scope.transition(card['id'],'validate',card['version'])
            scope.transition(card['id'],'adopt',card['version'])
