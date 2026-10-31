"""Offline review fixtures shared by review and manuscript tests."""
import json
from types import SimpleNamespace

import httpx
import pytest

from ai_system.research.control import graph_view
from ai_system.research.models import WorkerResult, ReviewRecord
from ai_system.research.review_migration import context_from_store
from ai_system.research.store import ResearchStore
from ai_system.research.validation import report_fingerprint
from research_tools_helpers import answer
from services.file_services.workspace_layout import workspace_path


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('Real HTTP is forbidden in offline review tests')
    async def refuse_async(*args, **kwargs):
        raise AssertionError('Real HTTP is forbidden in offline review tests')
    monkeypatch.setattr(httpx.Client, 'send', refuse)
    monkeypatch.setattr(httpx.AsyncClient, 'send', refuse_async)


@pytest.fixture
def env(tmp_path):
    store=ResearchStore(tmp_path,'reviews');(tmp_path/'data').mkdir();(tmp_path/'data/input.csv').write_text('x\n1\n')
    source='检查score是有限数。'
    analysis={'subproblems':[{'id':'Q1','title':'数值验证','question':source,'depends_on':[]}],
        'requirements':[{'id':'RQ1','kind':'constraint','statement':source,'source_quote':source,'task_ids':['Q1'],
            'checks':[{'id':'finite','description':'有限','left':{'task_id':'Q1','name':'score'},'operator':'finite'}]}],'assumptions':[]}
    store.initialize(source,analysis);order=store.claim('Q1');run=workspace_path(tmp_path, 'runs/one');run.mkdir(parents=True)
    (run/'input.py').write_text('print(1.0,2.0)');(run/'stdout.log').write_text('1.0 2.0\n')
    (workspace_path(tmp_path, 'manifest.json')).write_text(json.dumps({'files': {}, 'runs':{'one':{'status':'success','input':'runs/one/input.py','stdout':'runs/one/stdout.log'}}}))
    results=[{'id':'R'+str(i+1),'subproblem':'Q1','kind':'number','name':name,'value':str(value),'run_id':'one','path':'','description':'','unit':''} for i,(name,value) in enumerate([('score',1.0),('baseline',2.0)])]
    claims=[{'id':'C'+str(i+1),'subproblem':'Q1','statement':'结论 {{R:R'+str(i+1)+'}}','strength':'倾向','evidence':['R'+str(i+1)]} for i in range(2)]
    report={'status':'completed','summary':'实测score为 {{R:R1}}，基线为 {{R:R2}}。',
        'key_findings':[{'statement':c['statement'],'strength':c['strength']} for c in claims],
        'requirement_evidence':[{'requirement_id':'RQ1','evidence_refs':['R1']}]}
    store.commit(WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',results=results,claims=claims,report=report,
        reviews=[ReviewRecord(report_hash=report_fingerprint(report),issues=[])]))
    before=store.snapshot()
    answer(store,question_id=before['experiments']['Q1']['question_id'],
        statement='本次输入下score为 {{R:R1}}，满足有限数检查。',artifact_refs=['R1'],
        coverage='answered',stop_reason='冻结的有限数检查已通过。',limitations=['该检查不证明模型效果或外推有效性。'])
    snapshot=store.snapshot()
    assert snapshot['executions']==before['executions'] and snapshot['artifacts']==before['artifacts']
    assert store.delivery_ready(snapshot)
    state=graph_view(snapshot)
    state.update(sections={'abstract':'摘要正文。'*30,'evaluation':'证据局限。'*30},consistency=[])
    ctx=context_from_store(store,'offline-model');return SimpleNamespace(store=store,state=state,ctx=ctx)
