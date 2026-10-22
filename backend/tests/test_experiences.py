"""Cross-study advice with real SQLite/checks, real kernel evidence, no model calls."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from models.models import Base, User, Project
from services.research_experience import ExperienceScope
from ai_system.research.store import ResearchStore
from ai_system.research.models import WorkerResult
from ai_system.research.experience import conditions, prompt_context
from ai_system.graph.context import GraphContext

TASK='连续线性规划：最大化 3x+2y，约束 x+y<=4,x<=2,y<=3,x,y为非负连续实数；独立复核答案 x=2,z=10。'
DESIGN={'methods':['linear_programming'],'chosen':{'name':'线性规划','method_keys':['linear_programming'],
    'idea':'实际求解并检查','formulation':'linear constraints','steps':['读取当前问题','SciPy linprog 求解','代回原始约束和目标'],
    'validation':'核对本研究登记的 x 与 z'}}
CODE="from scipy.optimize import linprog\nr=linprog([-3,-2],A_ub=[[1,1],[1,0],[0,1]],b_ub=[4,2,3],bounds=[(0,None),(0,None)],method='highs')\nx=float(r.x[0]);z=float(-r.fun)\nprint(x,z)"


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
        fs.save_run_stdout(run_id,f'{x} {z}');fs.finish_run(run_id,'success')
    rows=[{'id':store.allocate_ref('R'),'name':k,'value':str(v),'subproblem':'Q1','kind':'number','run_id':run_id} for k,v in [('x',x),('z',z)]] if execution=='succeeded' else []
    result=WorkerResult(order=order,execution_status=execution,acceptance='accepted' if rows else 'inconclusive',
        design=DESIGN,results=rows,report={'status':'completed' if rows else 'failed','error':error,
            'requirement_evidence':[{'requirement_id':'RQ1','evidence_refs':[r['id'] for r in rows]}] if rows else []})
    return store.commit(result),result


def adopt_ready(scope):
    for card in scope.list()['cards']:
        if card['card']['level'] in {'verified','counterexample'} and card['status']!='adopted':
            scope.transition(card['id'],'validate',card['version'])
            scope.transition(card['id'],'adopt',card['version'])


def test_missing_or_changed_run_logs_stop_future_injection(env):
    a=env.store();_,r=submit(a);adopt_ready(env.scope());actual=conditions(a.root,TASK)
    assert env.scope('B').search(TASK,['linear_programming'],actual)
    manifest=json.loads((a.root/'manifest.json').read_text())
    run=manifest['runs'][r.results[0]['run_id']]
    (a.root/run['stdout']).unlink()
    assert not env.scope('B').search(TASK,['linear_programming'],actual)
    env.scope().rebuild()
    assert not env.scope('B').search(TASK,['linear_programming'],actual)


def test_changed_stdout_cannot_be_revalidated_by_rebuilding_index(env):
    a=env.store();_,r=submit(a);adopt_ready(env.scope());actual=conditions(a.root,TASK)
    assert env.scope('B').search(TASK,['linear_programming'],actual)
    manifest=json.loads((a.root/'manifest.json').read_text())
    (a.root/manifest['runs'][r.results[0]['run_id']]['stdout']).write_text('2 10\nchanged after commit')
    assert not env.scope('B').search(TASK,['linear_programming'],actual)
    env.scope().rebuild()
    assert not env.scope('B').search(TASK,['linear_programming'],actual)
    assert env.scope().list()['cards'][0]['card']['level']=='unconfirmed'


def test_revocation_during_commit_rolls_back_before_publication(env,monkeypatch):
    from ai_system.research.models import ResearchConflict
    a=env.store();submit(a);adopt_ready(env.scope());b=env.store('B');order=b.claim('Q1');scope=b.experience_scope
    ctx=GraphContext.create(str(b.root),planner_llm=None,experience_scope=scope,experience_store=b,experience_order=order)
    prompt_context(ctx,{'source':TASK,'subproblem':analysis()['subproblems'][0]},['linear_programming'],'solve')
    decisions=iter([True,False]);monkeypatch.setattr(scope,'valid_refs',lambda _:next(decisions))
    with pytest.raises(ResearchConflict,match='提交前经验已失效'):submit(b,order=order)
    assert not b.registry_payload()['results']
    assert 'commit' not in b.snapshot()['executions'][order.attempt_id]
