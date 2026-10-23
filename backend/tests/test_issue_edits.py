"""Actual store/editor/export paths with offline controlled model contracts; HTTP forbidden."""

from ai_system.research.domain import findings as research_findings
import asyncio,json
from types import SimpleNamespace
import pytest
from test_paper_editor import paper
from test_review_cache import no_network
from ai_system.graph.state import ClaimAudit,ClaimIssue
from ai_system.modules.writing.validation import assemble
from ai_system.research.models import IssueReport,ResearchConflict
from ai_system.research.review_cache import ReviewCache,review_context
from services.chat_services import paper_editor as editor,paper_corrections as edits,issue_edits as issues

@pytest.fixture
def linked(paper,monkeypatch,request):
    store,state=paper;old='本研究显著优于所有基线。';new='当前划分上误差较低，尚未开展显著性检验。';state['sections']['evaluation']='研究局限仍需在更多条件下进一步核查。'*8+old
    for key in state['sections']:
        if key.startswith('solve:'):
            task=key.split(':')[1];state['sections'][key]+=' '.join('{{R:'+r['id']+'}}' for r in store.registry_payload()['results'] if r['subproblem']==task)
    store.save_sections(state['sections'],[]);store.publish_paper(assemble(state))
    if getattr(request,'param',False):
        from helpers.paper import render_native_paper
        store.publish_paper(render_native_paper(store,state),filename='paper.tex');(store.root/'paper.pdf').write_bytes(b'%PDF-old-fixture')
    from services.chat_services import review_tasks
    review_state=review_tasks.paper_state(store)
    review_key='paper.tex' if getattr(request,'param',False) else 'evaluation'
    model=SimpleNamespace(model_name='offline-issue',calls=[],verdict='resolved',error=False)
    async def controlled(llm,schema,messages,validate=None,**kwargs):
        model.calls.append(schema.__name__)
        if model.error:raise TimeoutError('controlled')
        if schema is edits.FragmentReplacement:answer=schema(replacement=new)
        else:
            assert schema is issues.IssueAudit and '必须逐项核对的源意见' in messages[-1].content
            answer=schema(issue_id=source['id'],verdict=model.verdict,reason='已核对登记结果和修订全文。',issues=[])
        if validate:validate(answer)
        return answer
    monkeypatch.setattr(issues,'call_structured',controlled);monkeypatch.setattr(edits,'call_structured',controlled)
    ctx=edits.proposal_context(store,review_state,model);context=review_context(ctx,review_state,'sections','system','fixture',ClaimAudit,sections=review_state['sections'])
    cache=ReviewCache(store);r=cache.start('sections',context);cache.complete(r,ClaimAudit(issues=[ClaimIssue(section=review_key,problem='未经检验使用统计显著',quote=old)]).model_dump())
    source=store.report_issues([IssueReport(task_id='Q1',category='writing',section=review_key,message='未经检验使用统计显著')],'review-task:sections')[0]
    issues.bind_findings(store,[source['id']],review_state['sections'],source_version=review_state.get('source_version'))
    return SimpleNamespace(store=store,state=state,old=old,new=new,model=model,source=source)

def binding(v):
    c=issues.context(v.store,v.source['id']);return {'id':v.source['id'],'version':c['version']}
def propose(v,automatic=False):
    section=v.store.snapshot()['sections']['evaluation'];scope={'old':v.old,'section_revision':section['revision'],'section_hash':section['content_hash'],'source_hash':v.store.snapshot()['paper_hash']}
    return asyncio.run(editor.propose(v.store,'evaluation','按意见修正措辞',None if automatic else section['text'].replace(v.old,v.new),v.model,source_issue=binding(v),**({'fragment_scope':scope} if automatic else {})))
def current(v):return next(i for i in research_findings(v.store.snapshot()) if i['id']==v.source['id'])

@pytest.mark.parametrize('automatic',[False,True])
def test_issue_candidate_review_diff_and_apply_close_only_verified_source(linked,automatic):
    v=linked;before=v.store.snapshot();other=v.store.report_issues([IssueReport(task_id='Q1',category='writing',section='abstract',message='另一项写作意见')],'manual')[0]
    c=issues.context(v.store,v.source['id']);assert c['quote']==v.old and c['location']=='精确原文（唯一匹配）'
    p=propose(v,automatic);assert p['source_issue']['id']==v.source['id'] and p['review_kind']=='issue_section' and p['review_id'] and not p['review_issues']
    assert current(v)['status']=='confirmed' and (v.store.root/'paper.md').read_text().find(v.old)>=0
    applied=asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));assert applied['status']=='applied'
    source=current(v);assert source['status']=='resolved' and source['review_resolution']['candidate_id']==p['id'] and source['review_resolution']['review_id']==p['review_id']
    assert v.new in (v.store.root/'paper.md').read_text() and v.old not in (v.store.root/'paper.md').read_text()
    after=v.store.snapshot();assert all(before[k]==after[k] for k in ('experiments','executions','artifacts','claims','validations'))
    assert next(i for i in research_findings(after) if i['id']==other['id'])['status']=='confirmed'
    assert v.store.delivery_ready(after), 'an unrelated advisory finding must not block an answered study'
    assert v.model.calls==(['FragmentReplacement','IssueAudit'] if automatic else ['IssueAudit'])

@pytest.mark.parametrize('mode',['unresolved','uncertain','failure','reject'])
def test_non_success_never_closes_issue_or_changes_published_source(linked,mode):
    v=linked;before=(v.store.root/'paper.md').read_bytes()
    if mode in {'unresolved','uncertain'}:v.model.verdict=mode
    if mode=='failure':v.model.error=True
    p=propose(v)
    if mode=='reject':asyncio.run(editor.decide(v.store,p['id'],False,'markdown'))
    else:
        assert p['review_issues']
        with pytest.raises((ValueError,ResearchConflict)):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))
    assert current(v)['status']=='confirmed' and (v.store.root/'paper.md').read_bytes()==before


def test_stale_opinion_or_new_paper_cannot_receive_old_candidate(linked):
    v=linked;p=propose(v);old=binding(v);sections={k:s['text'] for k,s in v.store.snapshot()['sections'].items()};sections['evaluation']+='已改变。';v.store.save_sections(sections,[])
    with pytest.raises(ResearchConflict):asyncio.run(editor.decide(v.store,p['id'],True,'markdown'))
    with pytest.raises(ResearchConflict):issues.require_binding(v.store,old)
    c=issues.context(v.store,v.source['id']);assert not c['origin_current'] and c['quote'] is None
    assert current(v)['status']=='confirmed'


def test_review_proof_cannot_be_rebound_to_different_source(linked):
    v=linked;p=propose(v)
    p['filename']='paper.tex';source='old tex';(v.store.root/'paper.tex').write_text(source)
    from ai_system.research.store import digest
    with v.store._transaction() as db:
        state=v.store._read(db);state.update(paper_version='.system/papers/fixture.tex',paper_hash=digest(source));v.store._save(db,state,'fixture_native',{})
    p['source_hash']=digest(source);p['base_commit']=v.store.snapshot()['commit_seq'];p['source_issue']=binding(v)
    with v.store._transaction() as db:db.execute('UPDATE commands SET response=? WHERE id=?',(json.dumps(p),'paper-edit:'+p['id']))
    with pytest.raises(ResearchConflict):asyncio.run(editor.decide(v.store,p['id'],True,'latex'))
    assert current(v)['status']=='confirmed' and (v.store.root/'paper.tex').read_text()==source
