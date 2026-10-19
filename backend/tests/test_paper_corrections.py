"""Offline product corrections with real numeric estimator checks; no real HTTP."""
import asyncio,hashlib,json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from sklearn.linear_model import LinearRegression,Ridge
from sklearn.preprocessing import StandardScaler
from test_paper_editor import paper
from test_review_cache import no_network
from ai_system.research.method_facts import verified_methods,canonical_method_block
from ai_system.research.comparison import ComparisonStore
from ai_system.research.store import digest
from ai_system.research.validation import protocol
from ai_system.research.models import ResearchConflict
from ai_system.graph.nodes.estimator_contracts import check_estimator
from ai_system.graph.nodes import consistency
from ai_system.graph.state import ClaimAudit,ClaimIssue
from ai_system.graph.nodes.validate import assemble
from services.chat_services import paper_editor as editor,paper_corrections as corrections


@pytest.fixture
def verified_paper(paper,monkeypatch):
    store,state=paper
    candidates=[{'name':'OLS'},{'name':'Ridge'}]
    with store._transaction() as db:
        snapshot=store._read(db);snapshot['tasks']['Q1']['design']={'candidates':candidates}
        store._save(db,snapshot,'fixture-methods',{})
    snapshot=store.snapshot();task=snapshot['tasks']['Q1'];comparison=ComparisonStore(store);comparison.set_policy(True)
    branches=[];runs={}
    for i,(model,family,pre) in enumerate([(LinearRegression(),'ols',[]),(Ridge(alpha=1),'ridge',[])]):
        contract={'family':family,'standardize':False,'alpha':1.0 if family=='ridge' else None};audit=check_estimator(model,contract)
        code=f'# fixture estimator {family}';driver=f'# fixture project driver {family}';stdout='fixture audit complete\n';run_id=f'fixture-{i}'
        folder=store.root/'runs'/run_id;folder.mkdir(parents=True);(folder/'input.py').write_text(driver);(folder/'stdout.log').write_text(stdout)
        runs[run_id]={'status':'success','input':f'runs/{run_id}/input.py','stdout':f'runs/{run_id}/stdout.log'}
        branches.append({'index':i,'status':'valid','code':code,'code_hash':hashlib.sha256(code.encode()).hexdigest(),
            'implementation_audit':audit,'estimator_contract':contract,'run_ids':[run_id],
            'driver_hash':hashlib.sha256(driver.encode()).hexdigest(),'stdout_hash':hashlib.sha256(stdout.encode()).hexdigest()})
    record=comparison.create('Q1',task['revision'],{'branches':branches,'implementation_contract_version':1,'file_inputs':store.file_inputs(),
        'input_refs':snapshot['attempts'][task['active_attempt']]['input_refs'],'policy_version':comparison.policy()['version'],
        'candidate_hashes':[digest(c) for c in candidates],'contract_hash':digest(protocol(snapshot,'Q1')),'task_hash':digest(task['subproblem']), 'protocol_hash':'fixture-protocol'})
    for b in record['branches']:
        target=store.root/'.system/attempts'/task['active_attempt']/'workspace/outputs/intermediate/comparisons'/record['id']/f"predictions-{b['index']}.json"
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps({'estimator_audit':b['implementation_audit']}));b['prediction_hash']=hashlib.sha256(target.read_bytes()).hexdigest()
    comparison.save(record);(store.root/'manifest.json').write_text(json.dumps({'runs':runs}))
    for key in state['sections']:
        task_id=key.split(':',1)[1] if key.startswith('solve:') else None
        refs=[r['id'] for r in store.registry_payload()['results'] if r['subproblem']==task_id]
        state['sections'][key]+=' '.join('{{R:'+r+'}}' for r in refs)
    state['sections']['solve:Q1']+=r' 原公式 $y=X\beta$。'
    store.save_sections(state['sections'],[]);store.publish_paper(assemble(state))
    facts=verified_methods(store);assert len(facts)==2
    block=canonical_method_block(facts,'Q1');before=store.snapshot()['sections']['solve:Q1']['text'];after=before.replace(r'原公式 $y=X\beta$。',block)
    async def generate(llm,schema,messages,validate=None,**kwargs):
        answer=corrections.CorrectionEdits(edits=[corrections.AnchoredEdit(section='solve:Q1',old=r'原公式 $y=X\beta$。',verified_method=True),
            corrections.AnchoredEdit(section='abstract',old=state['sections']['abstract'],replacement=state['sections']['abstract'].replace('原来的','修订后的'))])
        validate(answer);return answer
    async def audit(*args,validate=None,**kwargs):
        answer=ClaimAudit(issues=[]);validate(answer);return answer
    monkeypatch.setattr(corrections,'call_structured',generate);monkeypatch.setattr(consistency,'call_structured',audit)
    return SimpleNamespace(store=store,state=state,record=record,comparison=comparison,after=after,block=block,llm=SimpleNamespace(model_name='offline-review'))


def proposed(v):return asyncio.run(editor.propose(v.store,'solve:Q1','修正方法说明和汇总措辞',llm=v.llm,edit_kind='evidence',section_keys=['solve:Q1','abstract']))


def test_reviewed_multi_section_candidate_and_atomic_apply_leave_experiments_unchanged(verified_paper):
    v=verified_paper;before=v.store.snapshot();p=proposed(v)
    assert not p['review_issues'] and p['review_id'] and v.store.snapshot()==before
    result=asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));after=v.store.snapshot()
    assert result['status']=='applied' and set(result['applied_revisions'])=={'solve:Q1','abstract'}
    for key in ['tasks','attempts','evidence','claims','validations']:assert before[key]==after[key]
    assert v.comparison.read('Q1',1)==v.record
    assert after['sections']['evaluation']==before['sections']['evaluation']
    assert v.block in after['sections']['solve:Q1']['text']
    asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));assert v.store.snapshot()==after


def test_actual_semantic_finding_blocks_candidate_without_mutating_published_research(verified_paper,monkeypatch):
    v=verified_paper;before=v.store.snapshot();source=(v.store.root/'paper.md').read_bytes()
    async def finding(*args,validate=None,**kwargs):
        answer=ClaimAudit(issues=[ClaimIssue(section='abstract',problem='未检验却声称统计显著')]);validate(answer);return answer
    monkeypatch.setattr(consistency,'call_structured',finding);p=proposed(v)
    assert p['review_issues']
    with pytest.raises(ValueError,match='审查问题'):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))
    assert v.store.snapshot()==before and (v.store.root/'paper.md').read_bytes()==source


def test_manual_multi_section_candidate_uses_same_guards_and_real_review_contract(verified_paper,monkeypatch):
    v=verified_paper;before=v.store.snapshot()
    async def forbidden(*args,**kwargs):raise AssertionError('Manual candidate must not call generation')
    monkeypatch.setattr(corrections,'call_structured',forbidden)
    contents={'solve:Q1':v.after,'abstract':before['sections']['abstract']['text'].replace('原来的','修订后的')}
    p=asyncio.run(editor.propose(v.store,'solve:Q1','手动按核验事实修正',llm=v.llm,edit_kind='evidence',section_keys=list(contents),section_contents=contents))
    assert p['generation_contract']=='manual-evidence-v1' and p['review_id'] and not p['review_issues']
    assert v.store.snapshot()==before
    result=asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));assert result['status']=='applied'
    assert v.store.snapshot()['attempts']==before['attempts']


def test_failed_candidate_review_is_not_a_success_or_global_service_issue(verified_paper,monkeypatch):
    v=verified_paper;before=v.store.snapshot()
    async def failure(*args,**kwargs):raise TimeoutError()
    monkeypatch.setattr(consistency,'call_structured',failure);p=proposed(v)
    assert p['review_issues'] and p['review_id'] is None
    assert v.store.snapshot()==before
    with pytest.raises(ValueError):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))


@pytest.mark.parametrize('change',['unverified','code','artifact','input','protocol','family'])
def test_unverified_or_stale_method_records_never_authorize_formula_corrections(verified_paper,change):
    v=verified_paper;row=v.record
    if change=='input':(v.store.root/'data').mkdir(exist_ok=True);(v.store.root/'data/new.csv').write_text('x\n1\n')
    elif change=='artifact':
        root=v.store.root/'.system/attempts'/v.store.snapshot()['tasks']['Q1']['active_attempt']/'workspace/outputs/intermediate/comparisons'/row['id'];(root/'predictions-0.json').write_text('{}');(root/'predictions-1.json').write_text('{}')
    else:
        if change=='protocol':row['contract_hash']='changed'
        for branch in row['branches']:
            if change=='unverified':branch['implementation_audit']['checked']=False
            elif change=='code':branch['code']+='changed'
            elif change=='family':branch['implementation_audit']['family']='unknown'
        with v.store._transaction() as db:db.execute('UPDATE comparisons SET payload=? WHERE id=?',(json.dumps(row),row['id']))
    assert not verified_methods(v.store)
    with pytest.raises((ValueError,ResearchConflict)):proposed(v)


def test_apply_rechecks_review_and_method_provenance(verified_paper):
    v=verified_paper;p=proposed(v);source=(v.store.root/'paper.md').read_bytes()
    with v.store._transaction() as db:db.execute("UPDATE review_records SET state='failed' WHERE id=?",(p['review_id'],))
    with pytest.raises(ResearchConflict,match='有效一致性审查'):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))
    assert (v.store.root/'paper.md').read_bytes()==source


def test_evidence_correction_api_preserves_explicit_multi_section_scope(verified_paper,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.work_routes import workspace
    from auth.auth import get_current_user
    from database.database import get_db
    v=verified_paper;calls=[]
    monkeypatch.setattr(workspace,'_paper_store',lambda *args,**kwargs:v.store)
    from ai_system.config import environment
    monkeypatch.setattr(environment,'setup_environment_from_db',lambda _:SimpleNamespace(config_manager=SimpleNamespace(get_model_config=lambda *args:object())))
    from ai_system.core_handlers import llm_providers
    monkeypatch.setattr(llm_providers,'create_llm_from_model_config',lambda _:v.llm)
    async def capture(*args,**kwargs):calls.append(kwargs);return {'id':'offline-candidate','status':'pending'}
    monkeypatch.setattr(editor,'propose',capture)
    app=FastAPI();app.include_router(workspace.router);app.dependency_overrides[get_current_user]=lambda:7;app.dependency_overrides[get_db]=lambda:object()
    with TestClient(app) as client:
        response=client.post('/api/workspace/fixture/paper/proposals',json={'research_id':v.store.research_id,'base_commit':v.store.snapshot()['commit_seq'],
            'section':'solve:Q1','edit_kind':'evidence','section_keys':['solve:Q1','abstract'],'instruction':'修正已有证据的表述'})
    assert response.status_code==200,response.text
    assert calls==[{'edit_kind':'evidence','section_keys':['solve:Q1','abstract']}]

    async def truncated(*args,**kwargs):raise RuntimeError('provider raw diagnostic must not be exposed')
    monkeypatch.setattr(editor,'propose',truncated)
    with TestClient(app) as client:
        response=client.post('/api/workspace/fixture/paper/proposals',json={'research_id':v.store.research_id,'base_commit':v.store.snapshot()['commit_seq'],
            'section':'solve:Q1','edit_kind':'evidence','section_keys':['solve:Q1'],'instruction':'修正'})
    assert response.status_code==409 and '原论文已保留' in response.json()['detail']
    assert 'provider raw diagnostic' not in response.text


def test_protected_method_block_does_not_allow_changing_observations(verified_paper):
    v=verified_paper;before=v.store.snapshot()['sections']['solve:Q1']['text']
    with pytest.raises(ValueError):corrections.guard_correction(before,v.after+' 观测值9',v.block,False)
    with pytest.raises(ValueError):corrections.guard_correction(before,v.after.replace('{{R:','{{R:R999'),v.block,False)
    with pytest.raises(ValueError):corrections.guard_correction(before,v.after.replace(v.block,'省略截距'),v.block,False)


@pytest.mark.parametrize('edits',[
    [{'section':'Q999','old':'original','replacement':'x'}],
    [{'section':'abstract','old':'not present','replacement':'x'}],
    [{'section':'abstract','old':'original','verified_method':True}],
    [{'section':'abstract','old':'original','replacement':'x'},{'section':'abstract','old':'original text','replacement':'y'}],
])
def test_anchored_changes_reject_unknown_missing_or_overlapping_fragments(edits):
    with pytest.raises(ValueError):corrections.apply_edits({'abstract':'original text'},corrections.CorrectionEdits.model_validate({'edits':edits}),{})


def test_centered_ols_and_scaled_ridge_formulas_reproduce_intercept_fits():
    rng=np.random.default_rng(31);x=rng.normal(size=(20,2))+[4,8];y=2+3*x[:,0]-x[:,1]+rng.normal(0,.1,20);v=rng.normal(size=(4,2))+[4,8]
    xc=x-x.mean(0);yc=y-y.mean();b=np.linalg.pinv(xc)@yc;b0=y.mean()-x.mean(0)@b
    assert np.allclose(b0+v@b,LinearRegression().fit(x,y).predict(v))
    scaler=StandardScaler().fit(x);z=scaler.transform(x);zc=z-z.mean(0);br=np.linalg.solve(zc.T@zc+np.eye(2),zc.T@yc);intercept=y.mean()-z.mean(0)@br
    assert np.allclose(intercept+scaler.transform(v)@br,Ridge(alpha=1).fit(z,y).predict(scaler.transform(v)))
    assert not np.allclose(v@np.linalg.lstsq(x,y,rcond=None)[0],b0+v@b)


def test_public_json_patch_is_parsed_locally_without_another_model_call():
    from langchain_core.messages import AIMessage,HumanMessage
    from ai_system.graph.llm_utils import call_structured
    class Model:
        calls=0
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,messages):
            self.calls+=1
            return {'raw':AIMessage(content=json.dumps({'edits':[{'section':'abstract','old':'显著优于','replacement':'实测误差较低'}]}),response_metadata={'finish_reason':'stop'}),'parsed':None,'parsing_error':ValueError('tool reply absent')}
    model=Model();answer=asyncio.run(call_structured(model,corrections.CorrectionEdits,[HumanMessage(content='offline')],retries=0))
    assert len(answer.edits)==1 and model.calls==1
