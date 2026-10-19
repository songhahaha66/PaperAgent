"""Exact fragment proposals, budgeted failure recovery and publication boundaries."""
import asyncio,json
from types import SimpleNamespace
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult,ChatGeneration,LLMResult
from test_paper_editor import paper
from test_review_cache import no_network
from services.chat_services import paper_editor as editor,paper_corrections as edits
from ai_system.graph.nodes import consistency
from ai_system.graph.nodes.validate import assemble
from ai_system.graph.state import ClaimAudit
from ai_system.graph.llm_utils import InvalidStructuredReply
from ai_system.research.store import digest
from ai_system.research.models import ResearchConflict
from ai_system.graph.tracing import LLMCallTrace


@pytest.fixture
def scoped(paper,monkeypatch):
    store,state=paper;key='evaluation';old='本研究的主要局限来自数据规模与实验设计。'
    for k in state['sections']:
        if k.startswith('solve:'):
            sid=k.split(':')[1];state['sections'][k]+=' '.join('{{R:'+r['id']+'}}' for r in store.registry_payload()['results'] if r['subproblem']==sid)
    state['sections'][key]='保留全文，不改这一段。\n\n'*1500+old+'\n\n保留结尾。'
    store.save_sections(state['sections'],[]);store.publish_paper(assemble(state))
    calls=[]
    async def generate(llm,schema,messages,validate=None,**kwargs):
        if schema is edits.FragmentAudit:
            reply=schema(verdict='equivalent',reason='原义及结论强度保持一致。')
            if validate:validate(reply)
            return reply
        assert kwargs['retries']==0 and schema is edits.FragmentReplacement
        assert len(messages[-1].content)<2400 and '保留全文' not in messages[-1].content
        calls.append(messages);reply=schema(replacement='主要局限源于数据规模和实验设计。');validate(reply);return reply
    async def review(*args,validate=None,**kwargs):
        reply=ClaimAudit(issues=[]);validate(reply);return reply
    monkeypatch.setattr(edits,'call_structured',generate);monkeypatch.setattr(consistency,'call_structured',review)
    return SimpleNamespace(store=store,state=state,key=key,old=old,calls=calls,llm=SimpleNamespace(model_name='offline'))


def request(v,old=None):
    section=v.store.snapshot()['sections'][v.key]
    return {'old':old or v.old,'section_revision':section['revision'],'section_hash':section['content_hash'],
        'source_hash':v.store.snapshot()['paper_hash']}


def propose(v,scope=None,recovery_of=None):
    return asyncio.run(editor.propose(v.store,v.key,'简洁表达，原意和证据不变',llm=v.llm,fragment_scope=scope or request(v),recovery_of=recovery_of))


def test_long_chapter_only_selected_short_sentence_changes_and_provenance_is_bound(scoped):
    v=scoped;before=v.store.snapshot();p=propose(v)
    assert p['origin']=='model' and p['generation_contract']=='scoped-anchored-edits-v2'
    assert len(v.calls)==1 and p['scope']['old_hash']==digest(v.old)
    expected=before['sections'][v.key]['text'].replace(v.old,'主要局限源于数据规模和实验设计。')
    assert p['after']==expected and v.store.snapshot()==before
    result=asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));after=v.store.snapshot()
    assert result['status']=='applied' and after['sections'][v.key]['text']==expected
    assert all(before[k]==after[k] for k in ('tasks','attempts','evidence','claims','validations'))
    assert all(before['sections'][k]==after['sections'][k] for k in before['sections'] if k!=v.key)


@pytest.mark.parametrize('change',['revision','section_hash','source_hash','missing','ambiguous'])
def test_bad_scope_is_rejected_before_any_generation(scoped,change):
    v=scoped;scope=request(v)
    if change=='revision':scope['section_revision']+=1
    elif change in {'section_hash','source_hash'}:scope[change]='f'*64
    elif change=='missing':scope['old']='原文中不存在的句子'
    else:scope['old']='保留全文'
    with pytest.raises((ValueError,ResearchConflict)):propose(v,scope)
    assert not v.calls


@pytest.mark.parametrize('replacement',['新数值 99','{{C:C999}}','全文替换。'*100])
def test_model_cannot_expand_scope_change_numbers_or_add_citations(scoped,monkeypatch,replacement):
    v=scoped;before=v.store.snapshot();calls=[]
    async def invalid(llm,schema,messages,validate=None,**kwargs):calls.append(1);reply=schema(replacement=replacement);validate(reply);return reply
    monkeypatch.setattr(edits,'call_structured',invalid)
    with pytest.raises(edits.PatchFailure):propose(v)
    assert calls==[1] and v.store.snapshot()==before


def test_truncation_stops_and_only_one_smaller_bound_recovery_is_allowed(scoped,monkeypatch):
    v=scoped;calls=[]
    async def truncate(*args,**kwargs):
        calls.append(1)
        cause=InvalidStructuredReply(ValueError('truncated'),AIMessage(content='partial',response_metadata={'finish_reason':'length'}))
        raise RuntimeError('cut') from cause
    monkeypatch.setattr(edits,'call_structured',truncate)
    with pytest.raises(edits.PatchFailure) as error:propose(v)
    failure=error.value.detail;assert failure['reason']=='truncated' and failure['recovery_allowed'] and calls==[1]
    for scope,parent in [(request(v),None),(request(v),failure['attempt_id']),(request(v,'数据规模与实验设计'),None)]:
        with pytest.raises(ValueError):propose(v,scope,parent)
    assert calls==[1]
    with pytest.raises(edits.PatchFailure) as second:propose(v,request(v,'数据规模与实验设计'),failure['attempt_id'])
    assert not second.value.detail['recovery_allowed'] and calls==[1,1]
    with pytest.raises(ValueError):propose(v,request(v,'数据规模'),second.value.detail['attempt_id'])
    assert calls==[1,1]


def test_manual_source_conflict_and_unrelated_candidate_changes_never_apply(scoped):
    v=scoped;p=propose(v);original=(v.store.root/'paper.md').read_bytes()
    p['after_sections'][v.key]+='片段外改动'
    with v.store._transaction() as db:db.execute('UPDATE commands SET response=? WHERE id=?',(json.dumps(p), 'paper-edit:'+p['id']))
    with pytest.raises(ResearchConflict,match='未选择片段'):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))
    assert (v.store.root/'paper.md').read_bytes()==original
    (v.store.root/'paper.md').write_text('手工稿不能覆盖')
    with pytest.raises(ResearchConflict):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))
    assert (v.store.root/'paper.md').read_text()=='手工稿不能覆盖'


def test_scope_cannot_cut_through_formula_or_reference(scoped):
    v=scoped;section=dict(v.store.snapshot()['sections'][v.key]);section['text']+=' $abc$ {{R:R1}}';section['content_hash']=digest(section['text'])
    scope=request(v,'abc');scope['section_hash']=section['content_hash']
    with pytest.raises(ValueError,match='边界'):edits.resolve_scope(v.key,section,(v.store.root/'paper.md').read_text(),scope)


def test_overlapping_repeated_fragment_is_ambiguous():
    answer=edits.CorrectionEdits(edits=[edits.AnchoredEdit(section='abstract',old='aaa',replacement='b')])
    with pytest.raises(ValueError,match='重叠'):edits.apply_edits({'abstract':'aaaaa'},answer,{})


def test_trace_records_reasoning_details_and_reply_sizes_without_text_or_secrets():
    trace=LLMCallTrace();from uuid import uuid4
    run=uuid4();trace.on_chat_model_start({},[],run_id=run,invocation_params={'max_tokens':2000,'api_key':'DO-NOT-STORE'})
    message=AIMessage(content='private final text',additional_kwargs={'reasoning_content':'private reasoning'},
        usage_metadata={'input_tokens':100,'output_tokens':60,'total_tokens':160,'output_token_details':{'reasoning':50}},response_metadata={'finish_reason':'stop'})
    trace.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]),run_id=run)
    call=trace.calls[0];assert call['reported_reasoning_tokens']==50 and call['output_limit']==2000
    assert call['reasoning_chars']==len('private reasoning') and call['public_reply_chars']==len('private final text')
    serialized=json.dumps(call);assert 'DO-NOT-STORE' not in serialized and 'private' not in serialized


def test_real_structured_adapter_never_retries_a_truncated_reply(scoped,monkeypatch):
    from ai_system.graph.llm_utils import call_structured
    v=scoped;monkeypatch.setattr(edits,'call_structured',call_structured)
    class TruncatedModel:
        calls=0
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,*args):
            self.calls+=1
            return {'raw':AIMessage(content='{"replacement":"partial',response_metadata={'finish_reason':'length'}),'parsed':None,'parsing_error':None}
    model=TruncatedModel();v.llm=model
    with pytest.raises(edits.PatchFailure) as error:propose(v)
    assert error.value.detail['reason']=='truncated' and model.calls==1


def test_candidate_audit_truncation_also_stops_without_publish(scoped,monkeypatch):
    from ai_system.graph.llm_utils import call_structured
    v=scoped;before=v.store.snapshot();original=edits.call_structured
    async def dispatch(llm,schema,messages,**kwargs):
        return await call_structured(llm,schema,messages,**kwargs) if schema is edits.FragmentAudit else await original(llm,schema,messages,**kwargs)
    monkeypatch.setattr(edits,'call_structured',dispatch)
    class Model:
        calls=0;model_name='offline'
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,*args):
            self.calls+=1
            return {'raw':AIMessage(content='partial',response_metadata={'finish_reason':'length'}),'parsed':None,'parsing_error':None}
    model=Model();v.llm=model;p=propose(v)
    assert model.calls==1 and p['review_issues'] and p['review_id'] is None
    assert v.store.snapshot()==before
    with pytest.raises(ValueError):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))


def test_generation_reserves_remaining_call_for_audit(scoped):
    from ai_system.research.runtime import ResearchRuntime
    v=scoped;runtime=ResearchRuntime(v.store.root,v.store.research_id);runtime.configure({'request_limit':1,'timeout_seconds':90,'compact_paper':False,'confirm_models':False})
    with pytest.raises(ValueError,match='两次'):propose(v)
    assert not v.calls and runtime.status()['used']==0


@pytest.mark.parametrize('verdict',['changed','uncertain'])
def test_local_semantic_change_or_uncertainty_remains_a_blocker(scoped,monkeypatch,verdict):
    v=scoped;original=edits.call_structured;before=v.store.snapshot()
    async def reject(llm,schema,messages,**kwargs):
        return schema(verdict=verdict,reason='原义没有得到确认。') if schema is edits.FragmentAudit else await original(llm,schema,messages,**kwargs)
    monkeypatch.setattr(edits,'call_structured',reject);p=propose(v)
    assert p['review_issues'] and p['review_id']
    with pytest.raises(ValueError):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))
    with pytest.raises(ResearchConflict):asyncio.run(edits.review_pending_fragment(v.store,p['id'],v.llm))
    assert v.store.snapshot()==before


def test_saved_model_proposal_can_resume_only_failed_legacy_full_review(scoped):
    from ai_system.research.review_cache import ReviewCache
    v=scoped;p=propose(v);state={**v.state,'sections':{**v.state['sections'],**p['after_sections']}}
    context=edits.review_binding(v.store,state,v.llm)
    cache=ReviewCache(v.store);record=cache.start('sections',context);cache.failed(record,TimeoutError())
    with v.store._transaction() as db:
        db.execute("DELETE FROM review_records WHERE stage='fragment'")
        p.update(review_id=None,review_kind='full_document',review_context_hash=digest(context),review_issues=[{'section':v.key,'message':'full review incomplete'}])
        db.execute('UPDATE commands SET response=? WHERE id=?',(json.dumps(p),'paper-edit:'+p['id']))
    before=v.store.snapshot();result=asyncio.run(edits.review_pending_fragment(v.store,p['id'],v.llm))
    assert result['origin']=='model' and result['review_kind']=='fragment_equivalence' and not result['review_issues']
    assert result['review_history'][0]['failed_record_id']==record['id']
    assert len(v.calls)==1 and v.store.snapshot()==before
    with pytest.raises(ResearchConflict):asyncio.run(edits.review_pending_fragment(v.store,p['id'],v.llm))
    applied=asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));assert applied['status']=='applied'


def test_scoped_apply_pdf_failure_preserves_old_files_and_versions(scoped,monkeypatch):
    from ai_system.graph.nodes.validate import assemble_latex
    v=scoped;v.store.publish_paper(assemble_latex(v.state),filename='paper.tex');(v.store.root/'paper.pdf').write_bytes(b'previous PDF')
    before=v.store.snapshot();original=(v.store.root/'paper.tex').read_bytes();p=propose(v)
    async def failure(folder):return SimpleNamespace(pdf_path=None,error='compiler failed')
    monkeypatch.setattr(editor,'compile_pdf',failure)
    with pytest.raises(ValueError,match='compiler failed'):asyncio.run(editor.decide(v.store,p['id'],True,'latex'))
    assert v.store.snapshot()==before and (v.store.root/'paper.tex').read_bytes()==original
    assert (v.store.root/'paper.pdf').read_bytes()==b'previous PDF' and editor.get_proposal(v.store,p['id'])['status']=='pending'


def test_existing_citation_marker_cannot_be_substituted(scoped,monkeypatch):
    v=scoped;old='已有背景 {{C:C1}}'
    section=dict(v.store.snapshot()['sections'][v.key]);section['text']+=old;section['content_hash']=digest(section['text'])
    source=(v.store.root/'paper.md').read_text();scope={'old':old,'section_revision':section['revision'],'section_hash':section['content_hash'],'source_hash':digest(source)}
    calls=[]
    async def wrong(llm,schema,messages,validate=None,**kwargs):
        calls.append(1);answer=schema(replacement='已有背景 {{C:C2}}');validate(answer);return answer
    monkeypatch.setattr(edits,'call_structured',wrong)
    with pytest.raises(edits.PatchFailure):asyncio.run(edits.generate_fragment(v.store,v.key,section,source,'保留引用',v.llm,scope))
    assert calls==[1]
