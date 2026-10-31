"""Long source-issue candidates through graph entry, recovery and atomic publication; no HTTP."""

from ai_system.research.domain import findings as research_findings
import asyncio,json
from types import SimpleNamespace
import pytest
from helpers.review import no_network
from ai_system.modules.writing import consistency as consistency
from ai_system.modules.writing.validation import assemble
from ai_system.graph.state import ClaimIssue,ClaimAudit
from services.chat_services import paper_editor as editor,paper_corrections as edits,issue_edits as issues,review_tasks

OLD='本研究显著优于所有基线。'
NEW='当前划分上误差较低，尚未开展显著性检验。'

@pytest.fixture
def longpaper(paper,monkeypatch,request):
    store,state=paper
    state['sections']['evaluation']='\n\n'.join('段落'+chr(65+i)+'。'+'研究条件和局限仍须审慎核对。'*90 for i in range(5))+'\n\n'+OLD
    for k in state['sections']:
        if k.startswith('solve:'):
            sid=k.split(':')[1];state['sections'][k]+=' '.join('{{R:'+r['id']+'}}' for r in store.registry_payload()['results'] if r['subproblem']==sid)
    from helpers.paper import render_native_paper
    store.save_sections(state['sections'],[]);store.publish_paper(render_native_paper(store,state) if getattr(request,'param',False) else assemble(state),filename='paper.tex' if getattr(request,'param',False) else 'paper.md')
    if getattr(request,'param',False):(store.root/'paper.pdf').write_bytes(b'%PDF-original-fixture')
    native=bool(getattr(request,'param',False))
    review_state=review_tasks.paper_state(store)
    review_key='paper.tex' if native else 'evaluation'
    other_key='paper.tex' if native else 'abstract'
    model=SimpleNamespace(model_name='offline-long-editor',calls=[],fail_target=False,verdict='resolved',new_finding=False)
    async def controlled(llm,schema,messages,validate=None,**kw):
        if schema is edits.FragmentReplacement:model.calls.append(('generate',None));answer=schema(replacement=NEW)
        elif schema is ClaimAudit:
            model.calls.append(('claims',None))
            assert OLD in messages[-1].content  # Tail is supplied without clipping.
            from ai_system.graph.state import SectionIssueReview
            marker='# 必须逐项重新核对的旧章节疑点\n'
            doubts=json.JSONDecoder().raw_decode(messages[-1].content.split(marker,1)[1].lstrip())[0] if marker in messages[-1].content else []
            answer=schema(issues=[] if doubts else [ClaimIssue(section=review_key,problem='未经检验使用统计显著',quote=OLD),
                                  ClaimIssue(section=other_key,problem='另一项既有写作意见')],
                issue_reviews=[SectionIssueReview(issue_id=doubt['id'],verdict='writing',reason='给定原文中的限制仍未补齐，继续保留具体写作问题。',
                    finding=ClaimIssue(section=doubt['section'],problem=doubt['message'],quote=OLD if doubt['section']==review_key else ''))
                    for doubt in doubts])
        else:
            assert schema is issues.IssueAudit
            model.calls.append(('target',None))
            if model.fail_target:raise TimeoutError('controlled source verdict interruption')
            answer=schema(issue_id=source['id'],verdict=model.verdict,reason='已核对源意见、完整目标节与登记证据。',issues=[ClaimIssue(section=review_key,problem='新引入的结论矛盾',quote=NEW)] if model.new_finding else [])
        if validate:validate(answer)
        return answer
    monkeypatch.setattr(consistency,'call_structured',controlled);monkeypatch.setattr(issues,'call_structured',controlled);monkeypatch.setattr(edits,'call_structured',controlled)
    monkeypatch.setattr(review_tasks,'call_structured',controlled)
    # An explicit user review uses the existing review-task entry even when
    # the draft has no registered claims. No scientific claim is fabricated.
    ctx=edits.proposal_context(store,review_state,model)
    reports,proof=asyncio.run(review_tasks.execute(store,'sections',model))
    assert proof
    assert any(r.section==review_key for r in reports)
    recorded=store.report_issues(reports,'review-task:sections')
    source=next(i for i in recorded if '未经检验使用统计显著' in i['message'])
    other=next(i for i in recorded if '另一项既有写作意见' in i['message'])
    issues.bind_findings(store,[i['id'] for i in recorded],review_state['sections'],source_version=review_state.get('source_version'))
    location=issues.context(store,source['id'])
    assert review_state['sections'][review_key].find(OLD)>6000
    if native:
        assert location['section']=='paper.tex' and location['origin_current']
        assert location['quote'] is None, 'A file-level finding must not claim a precise sentence anchor'
    else:assert location['quote']==OLD
    return SimpleNamespace(store=store,state=state,model=model,source=source,other=other,ctx=ctx)


def binding(v):return {'id':v.source['id'],'version':issues.context(v.store,v.source['id'])['version']}
def propose(v,automatic=True):
    s=v.store.snapshot()['sections']['evaluation']
    options={'fragment_scope':{'old':OLD,'section_revision':s['revision'],'section_hash':s['content_hash'],'source_hash':v.store.snapshot()['paper_hash']}} if automatic else {}
    return asyncio.run(editor.propose(v.store,'evaluation','按源意见修正',None if automatic else s['text'].replace(OLD,NEW),v.model,source_issue=binding(v),**options))
def current(v):return next(i for i in research_findings(v.store.snapshot()) if i['id']==v.source['id'])

@pytest.mark.parametrize('automatic',[True,False])
def test_tail_edit_reviews_full_target_once_and_publishes_with_source_verdict(longpaper,automatic):
    v=longpaper;before=v.store.snapshot();start=len(v.model.calls);p=propose(v,automatic)
    assert p['review_kind']=='issue_section' and p['review_id'] and not p['review_issues']
    assert current(v)['status']=='confirmed'
    calls=v.model.calls[start:];assert calls==([('generate',None),('target',None)] if automatic else [('target',None)])
    with v.store._connect() as db:r=json.loads(db.execute('SELECT payload FROM review_records WHERE id=?',(p['review_id'],)).fetchone()[0])
    assert r['result']['issue_id']==v.source['id'] and r['result']['verdict']=='resolved'
    assert r['context']['section_coverage']['sections']['evaluation']['included_chars']==len(p['after_sections']['evaluation'])
    result=asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));assert result['status']=='applied'
    after=v.store.snapshot();assert current(v)['status']=='resolved' and current(v)['review_resolution']['review_id']==r['id']
    assert next(i for i in research_findings(after) if i['id']==v.other['id'])['status']=='confirmed'
    assert all(before[k]==after[k] for k in ('experiments','executions','artifacts','claims','validations'))
    assert NEW in (v.store.root/'paper.md').read_text() and OLD not in (v.store.root/'paper.md').read_text()

def test_interrupted_candidate_restores_without_regeneration(longpaper):
    v=longpaper;v.model.fail_target=True;p=propose(v);assert p['review_issues'] and current(v)['status']=='confirmed'
    before=list(v.model.calls);v.model.fail_target=False
    restored=asyncio.run(edits.review_pending_fragment(v.store,p['id'],v.model));assert restored['review_id'] and not restored['review_issues']
    added=v.model.calls[len(before):];assert not any(k=='generate' for k,_ in added)
    assert added==[('target',None)]
    assert current(v)['status']=='confirmed'
    asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));assert current(v)['status']=='resolved'
