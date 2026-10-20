"""Current-source contracts; controlled models only, no API or full application run."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
from types import SimpleNamespace
import pytest
from test_review_cache import no_network
from test_paper_editor import paper
from ai_system.research.review_migration import context_from_store
from ai_system.graph.state import ClaimAudit, ClaimIssue
from ai_system.research.models import IssueReport, ResearchConflict
from ai_system.research.review_cache import ReviewCache, ReviewSuperseded, run_review, review_context
from ai_system.research.views import evidence_uses
from services.chat_services import paper_project as project, review_tasks, issue_edits, paper_editor
from ai_system.modules.writing import latex


@pytest.fixture
def native(paper):
    store,_=paper
    with store._transaction() as db:
        state=store._read(db);state['spec']['output_mode']='latex'
        store._save(db,state,'test_native',{})
    store.save_sections({'abstract':'过期数据库正文 {{R:R2}}'},[])
    (store.root/'paper.tex').write_text('\\newcommand{\\mine}{kept}\n\\begin{document}\n\\input{part}\n\\bibliography{refs}\n\\end{document}\n')
    (store.root/'part.tex').write_text('当前原文：结果有局限。\n% paperagent:evidence R1\n')
    (store.root/'refs.bib').write_text('@misc{source,title={Current source}}')
    return SimpleNamespace(store=store,state=review_tasks.paper_state(store),ctx=context_from_store(store,'offline-model'))


def test_reader_and_evidence_use_current_files_never_database_chapters(native):
    state=review_tasks.paper_state(native.store)
    assert set(state['sections'])=={'paper.tex','part.tex'}
    assert '过期数据库正文' not in '\n'.join(state['sections'].values())
    assert asyncio.run(paper_editor.document(native.store))['sections']==[]
    uses=evidence_uses(native.store.snapshot(),native.store.root)
    assert [(u['ref'],u['section']) for u in uses]==[('R1','part.tex')]
    assert evidence_uses(native.store.snapshot())==[]


@pytest.mark.parametrize('name',['part.tex','refs.bib'])
def test_project_changes_invalidate_completed_content_check(native,name):
    state=native.state;ctx=native.ctx
    before=review_context(ctx,state,'sections','system','prompt',ClaimAudit,sections=state['sections'])
    cache=ReviewCache(native.store);record=cache.start('sections',before)
    cache.complete(record,{'issues':[]})
    (native.store.root/name).write_text((native.store.root/name).read_text()+'\n用户更新')
    async def unused():raise AssertionError('stale input must not call a model')
    with pytest.raises(ReviewSuperseded):
        asyncio.run(run_review(ctx,state,'sections',ClaimAudit,'system','prompt',unused,sections=state['sections']))
    fresh=review_tasks.paper_state(native.store)
    after=review_context(ctx,fresh,'sections','system','prompt',ClaimAudit,sections=fresh['sections'])
    assert before['source_version']!=after['source_version']
    assert cache.get('sections',after,ClaimAudit) is None
    assert not review_tasks.matches({**record,'context':before},review_tasks.material(native.store,'sections'),'sections')


def test_late_content_review_cannot_pass_a_new_source_version(native):
    async def perform():
        (native.store.root/'part.tex').write_text('用户在检查期间修改的正文')
        return ClaimAudit(issues=[])
    with pytest.raises(ReviewSuperseded):
        asyncio.run(run_review(native.ctx,native.state,'sections',ClaimAudit,'s','p',perform,sections=native.state['sections']))
    assert not any(row['state']=='completed' for row in review_tasks.records(native.store))


def test_findings_bind_exact_file_quote_and_become_stale(native):
    issue=IssueReport(task_id='Q1',category='writing',section='part.tex',message='需限定结论范围')
    ids=[i['id'] for i in native.store.report_issues([issue],'sections')]
    state=native.state;context=review_context(native.ctx,state,'sections','s','p',ClaimAudit,sections=state['sections'])
    cache=ReviewCache(native.store);record=cache.start('sections',context)
    cache.complete(record,ClaimAudit(issues=[ClaimIssue(section='part.tex',quote='结果有局限',problem=issue.message,contradicts=True)]).model_dump())
    issue_edits.bind_findings(native.store,ids,state['sections'])
    current=issue_edits.context(native.store,ids[0])
    assert current['selection']['file']=='part.tex' and current['quote']=='结果有局限'
    project.bind(native.store.root,current['selection'])
    (native.store.root/'refs.bib').write_text('changed bibliography')
    stale=issue_edits.context(native.store,ids[0])
    assert not stale['origin_current'] and stale['selection'] is None and stale['quote'] is None


@pytest.mark.parametrize('race',[False,True])
def test_native_writer_uses_source_changes_and_retains_user_edits(native,monkeypatch,race):
    store=native.store;before=store.snapshot()['sections']
    async def noop(*args):pass
    ctx=SimpleNamespace(**native.ctx.__dict__,writer_llm=object(),template_requirements='',
        set_stage=noop,events=SimpleNamespace(file_changed=noop))
    async def reply(llm,schema,messages,validate):
        assert '过期数据库正文' not in messages[-1].content
        assert '当前原文' in messages[-1].content
        answer=latex.SourceEdits(edits=[latex.SourceEdit(file='part.tex',old='当前原文：结果有局限。',replacement='新正文：结果 {{R:R1}} 有局限。')])
        validate(answer)
        if race:(store.root/'part.tex').write_text('用户在生成期间的新编辑')
        return answer
    monkeypatch.setattr(latex,'call_structured',reply)
    if race:
        with pytest.raises(ResearchConflict):asyncio.run(latex.write(native.state,SimpleNamespace(context=ctx)))
        assert (store.root/'part.tex').read_text()=='用户在生成期间的新编辑'
    else:
        asyncio.run(latex.write(native.state,SimpleNamespace(context=ctx)))
        assert '新正文：结果 8' in (store.root/'part.tex').read_text()
        assert '\\newcommand{\\mine}{kept}' in (store.root/'paper.tex').read_text()
        change=project.read_records(store,'paper_changes')[-1]
        asyncio.run(project.decide(store,change['id'],'revert',compile_after=False))
        assert '当前原文' in (store.root/'part.tex').read_text()
    assert store.snapshot()['sections']==before


def test_check_status_uses_source_version_without_requiring_merge(native,monkeypatch):
    async def reply(llm,schema,messages,validate=None,**kwargs):
        answer=ClaimAudit(issues=[])
        if validate:validate(answer)
        return answer
    monkeypatch.setattr(review_tasks,'call_structured',reply)
    asyncio.run(review_tasks.execute(native.store,'sections',SimpleNamespace(model_name='offline-model')))
    def task():return next(t for t in review_tasks.status(native.store)['tasks'] if t['stage']=='sections')
    assert task()['state']=='completed'
    (native.store.root/'refs.bib').write_text('new bibliography version')
    assert task()['state']=='pending' and task()['can_resume']


def test_public_check_stage_does_not_reuse_old_pass(native):
    from ai_system.research.control import public_snapshot
    native.store.set_delivery_stage('validate','completed','旧版本通过')
    version=project.snapshot(native.store.root)['version']
    (native.store.root/'.system/validation.json').write_text(json.dumps({'source_version':version,'issues':[]}))
    assert public_snapshot(native.store.snapshot(),source_root=native.store.root)['delivery_stages']['validate']['status']=='completed'
    (native.store.root/'part.tex').write_text('updated source')
    assert public_snapshot(native.store.snapshot(),source_root=native.store.root)['delivery_stages']['validate']['status']=='pending'
