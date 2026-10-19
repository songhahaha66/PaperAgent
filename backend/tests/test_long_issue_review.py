"""Long source-issue candidates through graph entry, recovery and atomic publication; no HTTP."""

from ai_system.research.domain import findings as research_findings
import asyncio,copy,json
from types import SimpleNamespace
import pytest
from test_paper_editor import paper
from test_review_cache import no_network
from ai_system.modules.writing import consistency as consistency
from ai_system.modules.writing.validation import assemble,assemble_latex
from ai_system.graph.state import ClaimIssue
from ai_system.research import segmented_review as seg
from ai_system.research.models import ResearchConflict
from ai_system.research.store import digest
from services.chat_services import paper_editor as editor,paper_corrections as edits,issue_edits as issues,long_issue_review as long_review

OLD='本研究显著优于所有基线。'
NEW='当前划分上误差较低，尚未开展显著性检验。'

@pytest.fixture
def longpaper(paper,monkeypatch,request):
    store,state=paper
    state['sections']['evaluation']='\n\n'.join('段落'+chr(65+i)+'。'+'研究条件和局限仍须审慎核对。'*90 for i in range(5))+'\n\n'+OLD
    for k in state['sections']:
        if k.startswith('solve:'):
            sid=k.split(':')[1];state['sections'][k]+=' '.join('{{R:'+r['id']+'}}' for r in store.registry_payload()['results'] if r['subproblem']==sid)
    store.save_sections(state['sections'],[]);store.publish_paper(assemble_latex(state) if getattr(request,'param',False) else assemble(state),filename='paper.tex' if getattr(request,'param',False) else 'paper.md')
    if getattr(request,'param',False):(store.root/'paper.pdf').write_bytes(b'%PDF-original-fixture')
    model=SimpleNamespace(model_name='offline-long-editor',calls=[],fail_target=False,fail_block=False,verdict='resolved',new_finding=False)
    async def controlled(llm,schema,messages,validate=None,**kw):
        if schema is edits.FragmentReplacement:model.calls.append(('generate',None));answer=schema(replacement=NEW)
        elif schema is seg.BlockAudit:
            payload=json.loads(messages[-1].content.split('# 范围说明\n')[1]);core=payload['core'];model.calls.append(('block',digest(core)))
            if model.fail_block and NEW in core:raise TimeoutError('controlled missing block')
            found=[ClaimIssue(section=payload['section'],problem='未经检验使用统计显著',quote=OLD)] if OLD in core else []
            if model.new_finding and NEW in core:found.append(ClaimIssue(section=payload['section'],problem='新引入的结论矛盾',quote=NEW))
            answer=schema(issues=found,statements=[])
        elif schema is seg.GlobalAudit:
            model.calls.append(('global',None));ledger=json.loads(messages[-1].content[messages[-1].content.rfind('\n[')+1:])
            answer=schema(block_keys=[r['key'] for r in ledger],issues=[ClaimIssue(section='abstract',problem='另一项既有写作意见')])
        else:
            assert schema is issues.IssueAudit
            model.calls.append(('target',None))
            if model.fail_target:raise TimeoutError('controlled source verdict interruption')
            answer=schema(issue_id=source['id'],verdict=model.verdict,reason='已逐字核对源意见定位窗口与登记证据。',issues=[])
        if validate:validate(answer)
        return answer
    monkeypatch.setattr(seg,'call_structured',controlled);monkeypatch.setattr(long_review,'call_structured',controlled);monkeypatch.setattr(edits,'call_structured',controlled)
    # Exercise the actual graph audit entry, including its needed/segmented route.
    ctx=edits.proposal_context(store,state,model);reports=asyncio.run(consistency.audit_sections(state,ctx))
    assert any(r.section=='evaluation' for r in reports)
    recorded=store.report_issues(reports,'review-task:sections');source=next(i for i in recorded if i['section']=='evaluation');other=next(i for i in recorded if i['section']=='abstract')
    issues.bind_findings(store,[i['id'] for i in recorded],state['sections'])
    assert issues.context(store,source['id'])['quote']==OLD and state['sections']['evaluation'].find(OLD)>6000
    return SimpleNamespace(store=store,state=state,model=model,source=source,other=other,ctx=ctx)


def binding(v):return {'id':v.source['id'],'version':issues.context(v.store,v.source['id'])['version']}
def propose(v,automatic=True):
    s=v.store.snapshot()['sections']['evaluation']
    options={'fragment_scope':{'old':OLD,'section_revision':s['revision'],'section_hash':s['content_hash'],'source_hash':v.store.snapshot()['paper_hash']}} if automatic else {}
    return asyncio.run(editor.propose(v.store,'evaluation','按源意见修正',None if automatic else s['text'].replace(OLD,NEW),v.model,source_issue=binding(v),**options))
def current(v):return next(i for i in research_findings(v.store.snapshot()) if i['id']==v.source['id'])

@pytest.mark.parametrize('automatic',[True,False])
def test_tail_edit_reuses_other_blocks_and_publishes_only_with_both_proofs(longpaper,automatic):
    v=longpaper;before=v.store.snapshot();start=len(v.model.calls);p=propose(v,automatic)
    assert p['review_kind']=='issue_segmented' and p['review_id'] and not p['review_issues']
    assert current(v)['status']=='confirmed'
    calls=v.model.calls[start:];assert sum(c[0]=='block' for c in calls)==1 and sum(c[0]=='global' for c in calls)==1 and sum(c[0]=='target' for c in calls)==1
    with v.store._connect() as db:r=json.loads(db.execute('SELECT payload FROM review_records WHERE id=?',(p['review_id'],)).fetchone()[0])
    assert long_review.verify(v.store,r)
    result=asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));assert result['status']=='applied'
    after=v.store.snapshot();assert current(v)['status']=='resolved' and current(v)['review_resolution']['coverage_review_id']==r['provenance']['coverage_review_id']
    assert next(i for i in research_findings(after) if i['id']==v.other['id'])['status']=='confirmed'
    assert all(before[k]==after[k] for k in ('tasks','attempts','evidence','claims','validations'))
    assert NEW in (v.store.root/'paper.md').read_text() and OLD not in (v.store.root/'paper.md').read_text()

@pytest.mark.parametrize('where',['target','block'])
def test_interrupted_candidate_restores_without_regeneration_or_rechecking_unchanged(longpaper,where):
    v=longpaper;setattr(v.model,'fail_'+where,True);p=propose(v);assert not p['review_id'] and p['review_issues'] and current(v)['status']=='confirmed'
    before=list(v.model.calls);setattr(v.model,'fail_'+where,False)
    restored=asyncio.run(edits.review_pending_fragment(v.store,p['id'],v.model));assert restored['review_id'] and not restored['review_issues']
    added=v.model.calls[len(before):];assert not any(k=='generate' for k,_ in added)
    if where=='target':assert added==[('target',None)]
    else:assert sum(k=='block' for k,_ in added)==1
    assert current(v)['status']=='confirmed'
    asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));assert current(v)['status']=='resolved'

@pytest.mark.parametrize('verdict',['uncertain','unresolved'])
def test_actual_target_verdict_cannot_be_retried_until_it_says_resolved(longpaper,verdict):
    v=longpaper;v.model.verdict=verdict;p=propose(v);assert p['review_id'] and p['review_issues']
    with pytest.raises((ValueError,ResearchConflict)):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))
    with pytest.raises(ResearchConflict):asyncio.run(edits.review_pending_fragment(v.store,p['id'],v.model))
    assert current(v)['status']=='confirmed'

@pytest.mark.parametrize('damage',['child','global','target'])
def test_missing_coverage_or_source_verdict_prevents_publication(longpaper,damage):
    v=longpaper;p=propose(v);before=(v.store.root/'paper.md').read_bytes()
    with v.store._transaction() as db:
        composition=json.loads(db.execute('SELECT payload FROM review_records WHERE id=?',(p['review_id'],)).fetchone()[0]);proof=composition['provenance']
        if damage=='target':rid=proof['target_review_id']
        else:
            coverage=json.loads(db.execute('SELECT payload FROM review_records WHERE id=?',(proof['coverage_review_id'],)).fetchone()[0])
            rid=coverage['provenance']['blocks'][0]['review_id'] if damage=='child' else coverage['provenance']['global']['review_id']
        db.execute('DELETE FROM review_records WHERE id=?',(rid,))
    from ai_system.research.review_cache import ReviewCache
    with pytest.raises(ValueError,match='证明已失效'):ReviewCache(v.store).reuse(composition)
    with pytest.raises((ValueError,ResearchConflict)):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))
    assert current(v)['status']=='confirmed' and (v.store.root/'paper.md').read_bytes()==before


def test_old_version_and_new_findings_never_release_source_issue(longpaper):
    v=longpaper;v.model.new_finding=True;p=propose(v);assert not p['review_id'] and p['review_issues']
    v.model.new_finding=False;p=propose(v,False);text={k:s['text'] for k,s in v.store.snapshot()['sections'].items()};text['abstract']+='外部修改。';v.store.save_sections(text,[])
    with pytest.raises(ResearchConflict):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))
    assert current(v)['status']=='confirmed'


@pytest.mark.parametrize('longpaper',[True],indirect=True)
def test_pdf_failure_keeps_source_pdf_and_issue(longpaper,monkeypatch):
    from services.chat_services import paper_project as project
    from test_paper_interactions import selection
    v=longpaper;before=v.store.snapshot();source=(v.store.root/'paper.tex').read_bytes();pdf=(v.store.root/'paper.pdf').read_bytes()
    calls=list(v.model.calls);bound=selection(v.store,OLD,file='paper.tex')
    project.initialize(v.store)
    p=project.propose_change(v.store,None,None,[{**bound,'replacement':NEW}])
    async def failed(*a):return SimpleNamespace(pdf_path=None,error='controlled native compile failure')
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',failed)
    applied=asyncio.run(project.decide(v.store,p['id'],'apply'))
    expected=source.decode('utf-8').replace(OLD,NEW)
    assert applied['status']=='applied' and (v.store.root/'paper.tex').read_text()==expected
    assert v.store.snapshot()==before and current(v)['status']=='confirmed' and v.model.calls==calls
    assert (v.store.root/'paper.pdf').read_bytes()==pdf
    assert (v.store.root/project.VERSIONS/bound['version']/'paper.tex').read_bytes()==source
    assert project.describe(v.store)['compile']['state']=='failed'
    # Reverting the accepted draft also preserves the old PDF on compile failure;
    # neither operation supplies scientific review evidence or resolves the issue.
    reverted=asyncio.run(project.decide(v.store,p['id'],'revert'))
    assert reverted['status']=='reverted' and (v.store.root/'paper.tex').read_bytes()==source
    assert (v.store.root/'paper.pdf').read_bytes()==pdf and v.store.snapshot()==before


def test_graph_entry_routes_before_legacy_preflight_and_without_registered_claims(longpaper,monkeypatch):
    v=longpaper;v.ctx.registry.claims={}
    with v.store._transaction() as db:db.execute('DELETE FROM review_records')
    def forbidden(*a,**kw):raise AssertionError('Legacy completeness guard must not block segmented graph routing')
    monkeypatch.setattr(consistency,'require_complete_sections',forbidden)
    before=len(v.model.calls);reports=asyncio.run(consistency.audit_sections(v.state,v.ctx))
    assert any(r.section=='evaluation' for r in reports) and len(v.model.calls)>before


def test_normal_graph_validate_node_reaches_segmented_audit(longpaper):
    from ai_system.modules.writing import validation as validate_node
    v=longpaper;before=v.store.snapshot()
    with v.store._transaction() as db:db.execute('DELETE FROM review_records')
    result=asyncio.run(validate_node.validate(v.state,SimpleNamespace(context=v.ctx)))
    assert any(i['section']=='evaluation' for i in result['issues'])
    assert any(k=='global' for k,_ in v.model.calls)
    after=v.store.snapshot();assert all(before[k]==after[k] for k in ('tasks','attempts','evidence','claims','validations'))
