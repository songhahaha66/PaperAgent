"""Offline product corrections with real numeric estimator checks; no real HTTP."""
import asyncio,hashlib,json
from types import SimpleNamespace
import pytest
from sklearn.linear_model import LinearRegression,Ridge
from test_paper_editor import paper
from test_review_cache import no_network
from ai_system.research.method_facts import verified_methods,canonical_method_block
from ai_system.modules.experiments.store import ExperimentStore
from ai_system.research.store import digest
from ai_system.research.validation import protocol
from ai_system.research.models import ResearchConflict
from ai_system.modules.experiments.contracts import check_estimator
from ai_system.modules.writing import consistency as consistency
from ai_system.graph.state import ClaimAudit
from ai_system.modules.writing.validation import assemble
from services.chat_services import paper_editor as editor,paper_corrections as corrections


@pytest.fixture
def verified_paper(paper,monkeypatch):
    store,state=paper
    candidates=[{'name':'OLS'},{'name':'Ridge'}]
    with store._transaction() as db:
        snapshot=store._read(db);task=snapshot['experiments']['Q1'];snapshot['executions'][task['execution_id']]['result']['design']={'candidates':candidates}
        store._save(db,snapshot,'fixture-methods',{})
    snapshot=store.snapshot();task=snapshot['experiments']['Q1'];comparison=ExperimentStore(store);comparison.set_policy(True)
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
        'input_refs':snapshot['executions'][task['execution_id']]['input_refs'],'policy_version':comparison.policy()['version'],
        'candidate_hashes':[digest(c) for c in candidates],'contract_hash':digest(protocol(snapshot,'Q1')),'task_hash':digest(task['subproblem']), 'protocol_hash':'fixture-protocol'})
    for b in record['branches']:
        target=store.root/'.system/attempts'/task['execution_id']/'workspace/outputs/intermediate/comparisons'/record['id']/f"predictions-{b['index']}.json"
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


def proposed(v):return asyncio.run(editor.propose(v.store,'solve:Q1','修正方法说明和汇总措辞',llm=v.llm,edit_kind='evidence',section_keys=['solve:Q1','abstract'],section_contents={'solve:Q1':v.after,'abstract':v.state['sections']['abstract'].replace('原来的','修订后的')}))


def test_reviewed_multi_section_candidate_and_atomic_apply_leave_experiments_unchanged(verified_paper):
    v=verified_paper;before=v.store.snapshot();p=proposed(v)
    assert not p['review_issues'] and p['review_id'] and v.store.snapshot()==before
    result=asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));after=v.store.snapshot()
    assert result['status']=='applied' and set(result['applied_revisions'])=={'solve:Q1','abstract'}
    for key in ['experiments','executions','artifacts','claims','validations']:assert before[key]==after[key]
    assert v.comparison.read('Q1',1)==v.record
    assert after['sections']['evaluation']==before['sections']['evaluation']
    assert v.block in after['sections']['solve:Q1']['text']
    asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));assert v.store.snapshot()==after


def test_apply_rechecks_review_and_method_provenance(verified_paper):
    v=verified_paper;p=proposed(v);source=(v.store.root/'paper.md').read_bytes()
    with v.store._transaction() as db:db.execute("UPDATE review_records SET state='failed' WHERE id=?",(p['review_id'],))
    with pytest.raises(ResearchConflict,match='有效一致性审查'):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))
    assert (v.store.root/'paper.md').read_bytes()==source
