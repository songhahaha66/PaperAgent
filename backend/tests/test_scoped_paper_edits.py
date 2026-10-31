from services.file_services.workspace_layout import workspace_path
"""Exact fragment proposals, budgeted failure recovery and publication boundaries."""
import asyncio
from types import SimpleNamespace
import pytest
from helpers.review import no_network
from services.chat_services import paper_editor as editor,paper_corrections as edits
from ai_system.modules.writing import consistency as consistency
from ai_system.modules.writing.validation import assemble
from ai_system.graph.state import ClaimAudit
from ai_system.research.store import digest
from ai_system.research.models import ResearchConflict


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
    assert all(before[k]==after[k] for k in ('experiments','executions','artifacts','claims','validations'))
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


@pytest.mark.parametrize('conflict',[False,True])
def test_scoped_apply_pdf_failure_preserves_old_files_and_versions(scoped,monkeypatch,conflict):
    from helpers.paper import render_native_paper
    from services.chat_services import paper_project as project
    from helpers.paper import selection
    v=scoped;v.store.publish_paper(render_native_paper(v.store,v.state),filename='paper.tex');(v.store.root/'paper.pdf').write_bytes(b'previous PDF')
    before=v.store.snapshot();original=(v.store.root/'paper.tex').read_bytes()
    bound=selection(v.store,v.old,file='paper.tex');replacement='主要局限源于数据规模和实验设计。'
    project.initialize(v.store)
    p=project.propose_change(v.store,None,None,[{**bound,'replacement':replacement}])
    compiles=[]
    async def failure(*args, **kwargs):
        compiles.append(1)
        from ai_system.export.latex import LatexResult
        return LatexResult('paper.tex',error='compiler failed')
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',failure)
    if conflict:
        manual=original.decode('utf-8').replace(v.old,'人工修改的目标。')
        (v.store.root/'paper.tex').write_text(manual)
        with pytest.raises(ResearchConflict):asyncio.run(project.decide(v.store,p['id'],'apply'))
        assert (v.store.root/'paper.tex').read_text()==manual and not compiles
        assert project.read_records(v.store,'paper_changes')[0]['status']=='pending'
    else:
        applied=asyncio.run(project.decide(v.store,p['id'],'apply'))
        assert applied['status']=='applied' and compiles==[1]
        assert (v.store.root/'paper.tex').read_text()==original.decode('utf-8').replace(v.old,replacement)
        doc=project.describe(v.store)
        assert doc['compile']['state']=='failed' and 'compiler failed' in doc['compile']['message']
        assert (workspace_path(v.store.root, project.VERSIONS)/applied['applied_version']/'paper.tex').read_bytes()==(v.store.root/'paper.tex').read_bytes()
    assert (workspace_path(v.store.root, project.VERSIONS)/bound['version']/'paper.tex').read_bytes()==original
    assert v.store.snapshot()==before and not v.calls
    assert (v.store.root/'paper.pdf').read_bytes()==b'previous PDF'
