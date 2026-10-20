"""Cross-study advice with real SQLite/checks, real kernel evidence, no model calls."""

from ai_system.research.domain import findings as research_findings
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from models.models import Base,User,Project,ResearchExperience
from services.research_experience import ExperienceScope
from ai_system.research.store import ResearchStore,digest
from ai_system.research.models import WorkerResult, IssueReport
from ai_system.research.experience import card_for,conditions,prompt_context
from ai_system.graph.context import GraphContext
from ai_system.modules.experiments.execution import build_task

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




async def kernel_submit(store,code,order=None):
    order=order or store.claim('Q1');ctx=GraphContext.create(str(store.root),planner_llm=None)
    ctx.registry.allocate_id=store.allocate_ref
    try:
        run=await ctx.kernels.get('Q1').execute(code);assert run.ok,run.error
        x,z=[float(v) for v in run.stdout.split()[-2:]]
        rows=[asdict(ctx.registry.record_value(subproblem='Q1',name=k,value=str(v),run_id=run.run_id)) for k,v in [('x',x),('z',z)]]
        result=WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',design=DESIGN,results=rows,
            report={'status':'completed','requirement_evidence':[{'requirement_id':'RQ1','evidence_refs':[r['id'] for r in rows]}]})
        return store.commit(result),result
    finally:await ctx.kernels.close()




def test_empty_checks_success_is_unconfirmed_and_indexing_is_idempotent(env):
    s=env.store(checks=False);receipt,result=submit(s)
    assert s.snapshot()['executions'][result.order.attempt_id]['acceptance']=='accepted'
    cards=env.scope().list()['cards'];assert len(cards)==1 and cards[0]['card']['level']=='unconfirmed'
    before=(cards[0]['id'],cards[0]['version'],cards[0]['hash'])
    assert s.commit(result)==receipt
    assert env.scope().index(s)==0 and env.scope().rebuild()['updated']==0
    after=env.scope().list()['cards'][0];assert before==(after['id'],after['version'],after['hash'])




def test_owner_scope_conditions_methods_disable_delete_and_recovery(env):
    a=env.store('A');submit(a);adopt_ready(env.scope());card=env.scope().list()['cards'][0];actual=conditions(a.root,TASK)
    assert env.scope('B').search(TASK,['linear_programming'],actual)
    assert not env.scope('C',2).search(TASK,['linear_programming'],actual)
    assert not env.scope('B').search(TASK,['regression'],actual)
    assert not env.scope('B').search(TASK,['linear_programming'],{**actual,'structure':'integer'})
    with pytest.raises(PermissionError):env.scope('A',2).list()
    with pytest.raises(KeyError):env.scope('C',2).change(card['id'],delete=True)
    env.scope().change(card['id'],enabled=False);env.scope().rebuild()
    assert not env.scope('B').search(TASK,['linear_programming'],actual)
    env.scope().change(card['id'],enabled=True)
    assert env.scope('B').search(TASK,['linear_programming'],actual)
    env.scope().change(card['id'],delete=True);a.recover();env.scope().rebuild()
    assert not env.scope().list()['cards']
    with env.sessions() as db:assert db.get(ResearchExperience,card['id']).deleted


@pytest.mark.parametrize('execution,error,level',[('failed','ValueError: wrong columns','execution_error'),('failed','Timeout: cancelled','unconfirmed'),('cancelled','','unconfirmed')])
def test_failure_categories_never_imply_invalid_method(env,execution,error,level):
    s=env.store();_,r=submit(s,execution=execution,error=error)
    c=card_for(s.snapshot(),r.order.attempt_id)
    assert c['level']==level and '方法无效' in ' '.join(c['not_applicable'])


def test_index_failure_does_not_rollback_research_and_can_rebuild(env,monkeypatch):
    s=env.store();scope=s.experience_scope
    monkeypatch.setattr(scope,'index',lambda _:(_ for _ in ()).throw(RuntimeError('projection unavailable')))
    receipt,_=submit(s);assert receipt['published'] and s.registry_payload()['results']
    assert not env.scope().list()['cards'];assert env.scope().rebuild()['updated']==1


def test_fabricated_or_contract_mismatched_checks_never_make_verified_card(env):
    s=env.store();_,r=submit(s)
    state=s.snapshot();a=state['executions'][r.order.attempt_id]
    a['validation']['protocol_hash']='wrong';assert card_for(state,r.order.attempt_id)['level']!='verified'
    state=s.snapshot();a=state['executions'][r.order.attempt_id];a['validation']['checks'][0]['evidence_refs']=[]
    assert card_for(state,r.order.attempt_id)['level']!='verified'
    state=s.snapshot();state['artifacts'][r.results[0]['id']]['payload']['value']='999'
    assert card_for(state,r.order.attempt_id)['level']!='verified'


def test_checks_without_grounded_successful_run_are_unconfirmed(env):
    s=env.store();_,r=submit(s,ground=False)
    assert env.scope().list()["cards"][0]["card"]["level"]=="unconfirmed"


def test_missing_or_changed_run_logs_stop_future_injection(env):
    a=env.store();_,r=submit(a);adopt_ready(env.scope());actual=conditions(a.root,TASK)
    assert env.scope('B').search(TASK,['linear_programming'],actual)
    manifest=json.loads((a.root/'manifest.json').read_text())
    run=manifest['runs'][r.results[0]['run_id']]
    (a.root/run['stdout']).unlink()
    assert not env.scope('B').search(TASK,['linear_programming'],actual)
    env.scope().rebuild()
    assert not env.scope('B').search(TASK,['linear_programming'],actual)


def test_attempt_context_reads_root_literature_and_carries_authenticated_experience_scope(env):
    from ai_system.research.control import worker_context
    from ai_system.research.citations import literature_root
    from ai_system.research.literature import literature_context
    from test_literature import seed
    s=env.store();_,_,e=seed(s.root)
    ctx=GraphContext.create(str(s.root),planner_llm=None,experience_scope=env.scope())
    ctx.research=s;order=s.claim('Q1');local=worker_context(ctx,order)
    assert local.workspace!=s.root and local.research is None
    assert literature_root(local)==s.root and e['text'] in literature_context(literature_root(local),'linear')
    assert local.experience_scope is ctx.experience_scope and local.experience_store is s


def test_changed_stdout_cannot_be_revalidated_by_rebuilding_index(env):
    a=env.store();_,r=submit(a);adopt_ready(env.scope());actual=conditions(a.root,TASK)
    assert env.scope('B').search(TASK,['linear_programming'],actual)
    manifest=json.loads((a.root/'manifest.json').read_text())
    (a.root/manifest['runs'][r.results[0]['run_id']]['stdout']).write_text('2 10\nchanged after commit')
    assert not env.scope('B').search(TASK,['linear_programming'],actual)
    env.scope().rebuild()
    assert not env.scope('B').search(TASK,['linear_programming'],actual)
    assert env.scope().list()['cards'][0]['card']['level']=='unconfirmed'


def test_known_data_columns_must_match_before_reusing(env):
    a=env.store();(a.root/"data").mkdir(exist_ok=True);(a.root/"data/input.csv").write_text("demand,capacity\n1,2\n")
    submit(a)
    adopt_ready(env.scope())
    known=conditions(a.root,TASK)
    assert env.scope("B").search(TASK,["linear_programming"],known)
    assert not env.scope("B").search(TASK,["linear_programming"],{**known,"columns":["temperature","date"]})


def test_revocation_during_commit_rolls_back_before_publication(env,monkeypatch):
    from ai_system.research.models import ResearchConflict
    a=env.store();submit(a);adopt_ready(env.scope());b=env.store('B');order=b.claim('Q1');scope=b.experience_scope
    ctx=GraphContext.create(str(b.root),planner_llm=None,experience_scope=scope,experience_store=b,experience_order=order)
    prompt_context(ctx,{'source':TASK,'subproblem':analysis()['subproblems'][0]},['linear_programming'],'solve')
    decisions=iter([True,False]);monkeypatch.setattr(scope,'valid_refs',lambda _:next(decisions))
    with pytest.raises(ResearchConflict,match='提交前经验已失效'):submit(b,order=order)
    assert not b.registry_payload()['results']
    assert 'commit' not in b.snapshot()['executions'][order.attempt_id]
