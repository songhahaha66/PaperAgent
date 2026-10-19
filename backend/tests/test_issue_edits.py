"""Actual store/editor/export paths with offline controlled model contracts; HTTP forbidden."""
import asyncio,json
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_paper_editor import paper
from test_review_cache import no_network
from ai_system.graph.state import ClaimAudit,ClaimIssue
from ai_system.graph.nodes import consistency
from ai_system.graph.nodes.validate import assemble
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
        from ai_system.graph.nodes.validate import assemble_latex
        store.publish_paper(assemble_latex(state),filename='paper.tex');(store.root/'paper.pdf').write_bytes(b'%PDF-old-fixture')
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
    ctx=edits.proposal_context(store,state,model);context=review_context(ctx,state,'sections','system','fixture',ClaimAudit,sections=state['sections'])
    cache=ReviewCache(store);r=cache.start('sections',context);cache.complete(r,ClaimAudit(issues=[ClaimIssue(section='evaluation',problem='未经检验使用统计显著',quote=old)]).model_dump())
    source=store.report_issues([IssueReport(task_id='Q1',category='writing',section='evaluation',message='未经检验使用统计显著')],'review-task:sections')[0]
    issues.bind_findings(store,[source['id']],state['sections'])
    return SimpleNamespace(store=store,state=state,old=old,new=new,model=model,source=source)

def binding(v):
    c=issues.context(v.store,v.source['id']);return {'id':v.source['id'],'version':c['version']}
def propose(v,automatic=False):
    section=v.store.snapshot()['sections']['evaluation'];scope={'old':v.old,'section_revision':section['revision'],'section_hash':section['content_hash'],'source_hash':v.store.snapshot()['paper_hash']}
    return asyncio.run(editor.propose(v.store,'evaluation','按意见修正措辞',None if automatic else section['text'].replace(v.old,v.new),v.model,source_issue=binding(v),**({'fragment_scope':scope} if automatic else {})))
def current(v):return next(i for i in v.store.snapshot()['issues'] if i['id']==v.source['id'])

@pytest.mark.parametrize('automatic',[False,True])
def test_issue_candidate_review_diff_and_apply_close_only_verified_source(linked,automatic):
    v=linked;before=v.store.snapshot();other=v.store.report_issues([IssueReport(task_id='Q1',category='writing',section='abstract',message='另一项写作意见')],'manual')[0]
    c=issues.context(v.store,v.source['id']);assert c['quote']==v.old and c['location']=='精确原文（唯一匹配）'
    p=propose(v,automatic);assert p['source_issue']['id']==v.source['id'] and p['review_kind']=='issue_section' and p['review_id'] and not p['review_issues']
    assert current(v)['status']=='confirmed' and (v.store.root/'paper.md').read_text().find(v.old)>=0
    applied=asyncio.run(editor.decide(v.store,p['id'],True,'markdown'));assert applied['status']=='applied'
    source=current(v);assert source['status']=='resolved' and source['review_resolution']['candidate_id']==p['id'] and source['review_resolution']['review_id']==p['review_id']
    assert v.new in (v.store.root/'paper.md').read_text() and v.old not in (v.store.root/'paper.md').read_text()
    after=v.store.snapshot();assert all(before[k]==after[k] for k in ('tasks','attempts','evidence','claims','validations'))
    assert next(i for i in after['issues'] if i['id']==other['id'])['status']=='confirmed' and not v.store.delivery_ready(after)
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


def test_absent_or_inexact_quote_is_chapter_only_and_science_routes_to_research(linked):
    v=linked
    with v.store._transaction() as db:
        state=v.store._read(db);i=next(i for i in state['issues'] if i['id']==v.source['id']);i.pop('paper_origin');v.store._save(db,state,'fixture_legacy',{})
    c=issues.context(v.store,v.source['id']);assert c['quote'] is None and '仅定位章节' in c['location']
    science=v.store.report_issues([IssueReport(task_id='Q1',category='model',message='实际数据与方法需要核验',evidence_refs=['R1'])],'local_review')[0]
    c=issues.context(v.store,science['id']);assert c['route']=='research' and c['evidence']
    with pytest.raises(ResearchConflict,match='科研问题'):issues.require_binding(v.store,{'id':science['id'],'version':c['version']})
    with pytest.raises(ResearchConflict):propose(v)
    assert not v.model.calls


def test_long_sections_cannot_be_declared_full_pass_or_resolve_source(linked):
    v=linked;sections={k:s['text'] for k,s in v.store.snapshot()['sections'].items()};sections['evaluation']=v.old+'尾部。'*2500;v.store.save_sections(sections,[])
    with v.store._transaction() as db:
        state=v.store._read(db);i=next(i for i in state['issues'] if i['id']==v.source['id']);i.pop('paper_origin');v.store._save(db,state,'fixture_legacy',{})
    from services.chat_services.review_tasks import status
    t=next(t for t in status(v.store)['tasks'] if t['stage']=='sections');assert t['state']=='partial' and t['outcome']=='incomplete' and t['segmented']['completed_blocks']==0
    with pytest.raises(consistency.ReviewCoverageIncomplete):propose(v)
    assert not v.model.calls and current(v)['status']=='confirmed'
    ctx=edits.proposal_context(v.store,v.state,v.model);context=review_context(ctx,v.state,'sections','system','fixture',ClaimAudit,sections=sections)
    cache=ReviewCache(v.store);r=cache.start('sections',context)
    with pytest.raises(consistency.ReviewCoverageIncomplete):cache.complete(r,{'issues':[]})


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


def test_issue_endpoint_owner_and_missing_guards(linked,monkeypatch):
    from routers.work_routes import workspace
    from auth.auth import get_current_user
    from database.database import get_db
    v=linked;app=FastAPI();app.include_router(workspace.router);app.dependency_overrides[get_current_user]=lambda:7;app.dependency_overrides[get_db]=lambda:object()
    monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(created_by=7));monkeypatch.setattr(workspace.workspace_file_service,'get_workspace_path',lambda *a:v.store.root)
    with TestClient(app) as client:
        path='/api/workspace/work/paper/issues/'+v.source['id'];assert client.get(path).json()['quote']==v.old
        assert client.get('/api/workspace/work/paper/issues/unknown').status_code==404
        monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(created_by=8));assert client.get(path).status_code==403


@pytest.mark.parametrize('linked',[True],indirect=True)
def test_failed_pdf_export_keeps_issue_source_and_pdf(linked,monkeypatch):
    v=linked;p=propose(v);source=(v.store.root/'paper.tex').read_bytes();pdf=(v.store.root/'paper.pdf').read_bytes()
    async def failed(*a):return SimpleNamespace(pdf_path=None,error='controlled export failed')
    monkeypatch.setattr(editor,'compile_pdf',failed)
    with pytest.raises(ValueError,match='controlled export'):asyncio.run(editor.decide(v.store,p['id'],True,'latex'))
    assert current(v)['status']=='confirmed' and (v.store.root/'paper.tex').read_bytes()==source and (v.store.root/'paper.pdf').read_bytes()==pdf


@pytest.mark.parametrize('quote',['根本不存在的引文','aaa'])
def test_invalid_or_overlapping_quote_never_becomes_exact_location(linked,quote):
    v=linked
    with v.store._transaction() as db:
        state=v.store._read(db);i=next(i for i in state['issues'] if i['id']==v.source['id']);i.pop('paper_origin')
        if quote=='aaa':state['sections']['evaluation']['text']+=' aaaaa';from ai_system.research.store import digest;state['sections']['evaluation']['content_hash']=digest(state['sections']['evaluation']['text'])
        v.store._save(db,state,'fixture_quotes',{})
    text=v.store.snapshot()['sections']['evaluation']['text'];state={**v.state,'sections':{**v.state['sections'],'evaluation':text}}
    ctx=edits.proposal_context(v.store,state,v.model);material=review_context(ctx,state,'sections','fixture','quote test',ClaimAudit,sections=state['sections'])
    cache=ReviewCache(v.store);r=cache.start('sections',material);cache.complete(r,ClaimAudit(issues=[ClaimIssue(section='evaluation',problem='未经检验使用统计显著',quote=quote)]).model_dump())
    issues.bind_findings(v.store,[v.source['id']],state['sections']);assert issues.context(v.store,v.source['id'])['quote'] is None


def test_scientific_revision_rejects_stale_issue_before_any_planner_call(linked,monkeypatch):
    from ai_system.graph.agent import PaperGraphAgent
    from ai_system.research.planner import Planner
    v=linked;source=v.store.report_issues([IssueReport(task_id='Q1',category='model',message='需要科学核验',evidence_refs=['R1'])],'local_review')[0]
    c=issues.context(v.store,source['id']);agent=PaperGraphAgent(v.model,workspace_dir=str(v.store.root))
    async def refuse(*a,**kw):raise AssertionError('Stale issue must not invoke planner')
    monkeypatch.setattr(Planner,'amendment',refuse)
    with pytest.raises((ResearchConflict,ValueError)):
        asyncio.run(agent.revise_research(command_id='stale-source',research_id=v.store.research_id,base_revision=v.store.snapshot()['revision'],change='核查数据',scope=['Q1'],source_issue={'id':source['id'],'version':'f'*64}))
    with pytest.raises((ResearchConflict,ValueError)):
        asyncio.run(agent.revise_research(command_id='wrong-scope',research_id=v.store.research_id,base_revision=v.store.snapshot()['revision'],change='核查数据',scope=['Q2'],source_issue={'id':source['id'],'version':c['version']}))
    assert next(i for i in v.store.snapshot()['issues'] if i['id']==source['id'])['status']=='suspected'


def test_revision_command_cannot_be_rebound_to_different_source_issue(linked):
    from ai_system.research.planner import propose_revision
    v=linked;command={'command_id':'bound-source','base_revision':v.store.snapshot()['revision'],'change':'复核数据适用性','scope':['Q1']}
    # The existing command signature includes source identity without changing ordinary revision commands.
    from ai_system.research.models import Requirement
    requirements=[Requirement(id='RQ4',kind='goal',statement=command['change'],source_quote=command['change'],task_ids=['Q1'])];patch=propose_revision(v.store.snapshot(),**command,requirements=requirements)
    source={'id':'original','version':'a'*64};v.store.apply_revision_command(**command,patch=patch,source_issue=source)
    assert v.store.load_revision_command(**command,source_issue=source)
    with pytest.raises(ResearchConflict):v.store.load_revision_command(**command,source_issue={'id':'different','version':'a'*64})
    with pytest.raises(ResearchConflict):v.store.load_revision_command(**command)
