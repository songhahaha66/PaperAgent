import asyncio,json
from langchain_core.messages import AIMessage
import pytest
from test_review_cache import no_network
from test_literature import seed
from ai_system.research.citations import audit_citations,citation_issues,SupportBatch,SupportVerdict
from ai_system.research.review_cache import ReviewCache
from ai_system.research.store import ResearchStore


class Reply:
    model_name='offline-model'
    def __init__(self,kind='json'):self.kind=kind;self.calls=0
    def with_structured_output(self,*args,**kwargs):
        assert kwargs['include_raw'] is True
        return self
    async def ainvoke(self,messages):
        self.calls+=1
        if self.kind=='gateway':raise TimeoutError()
        rows=json.loads(messages[-1].content)
        data={'verdicts':[{'key':('C999' if self.kind=='wrong_key' else row['key']),
            'status':'unsupported' if self.kind=='unsupported' else 'supported','reason':'Only source support',
            'supporting_quote':row['excerpt']} for row in rows]}
        if self.kind=='missing':return None
        if self.kind=='empty':data={'verdicts':[]}
        return {'raw':AIMessage(content=json.dumps(data),response_metadata={'finish_reason':'stop'}),'parsed':None,'parsing_error':None}


def test_valid_plain_json_is_recovered_locally_and_unchanged_claim_is_cached(tmp_path):
    _,_,e=seed(tmp_path);text='来源中的背景说明 {{C:'+e['id']+'}}';model=Reply()
    result=asyncio.run(audit_citations(tmp_path,[text],model))
    assert result[0]['status']=='supported' and not citation_issues(tmp_path,text)
    asyncio.run(audit_citations(tmp_path,[text],model));assert model.calls==1
    store=ResearchStore(tmp_path,'workspace-literature')
    with store._connect() as db:
        record=json.loads(db.execute("SELECT payload FROM review_records WHERE state='completed'").fetchone()[0])
    assert record['provenance']['response_diagnostic']['local_json_recovery'] is True
    changed='改变引用主张的范围 {{C:'+e['id']+'}}'
    asyncio.run(audit_citations(tmp_path,[changed],model));assert model.calls==2


@pytest.mark.parametrize('kind,code',[('wrong_key','verdict_key_mismatch'),('missing','structured_reply_missing'),('empty','verdict_key_mismatch'),('gateway',None)])
def test_invalid_response_never_becomes_success_and_failure_has_diagnostic(tmp_path,kind,code):
    _,_,e=seed(tmp_path);text='待核验的背景 {{C:'+e['id']+'}}';model=Reply(kind)
    asyncio.run(audit_citations(tmp_path,[text],model))
    assert citation_issues(tmp_path,text)
    store=ResearchStore(tmp_path,'workspace-literature')
    with store._connect() as db:
        assert not db.execute("SELECT 1 FROM review_records WHERE state='completed'").fetchone()
        record=json.loads(db.execute("SELECT payload FROM review_records WHERE state='failed'").fetchone()[0])
    assert record['diagnostic']['contract_error']==code
    assert record['error_code']=='TimeoutError' if kind=='gateway' else record['error_code']=='CitationContractError'


def test_successful_unsupported_verdict_still_blocks_publication(tmp_path):
    _,_,e=seed(tmp_path);text='未被来源支持的主张 {{C:'+e['id']+'}}'
    asyncio.run(audit_citations(tmp_path,[text],Reply('unsupported')))
    assert citation_issues(tmp_path,text)


def test_changed_source_and_corrupted_cache_cannot_reuse_supported_verdict(tmp_path):
    library,_,e=seed(tmp_path);text='原始背景 {{C:'+e['id']+'}}';model=Reply()
    asyncio.run(audit_citations(tmp_path,[text],model))
    with library.research._transaction() as db:
        passage=json.loads(db.execute('SELECT payload FROM literature_passages WHERE id=1').fetchone()[0])
        passage['text']+=' New source text.'
        # Even a stale content_hash cannot conceal a relevant source edit.
        db.execute('UPDATE literature_passages SET payload=? WHERE id=1',(json.dumps(passage),))
    assert citation_issues(tmp_path,text)
    asyncio.run(audit_citations(tmp_path,[text],model));assert model.calls==2
