"""Current-source contracts; controlled models only, no API or full application run."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
import pytest
from test_review_cache import no_network
from test_paper_editor import paper
from ai_system.research.review_migration import context_from_store
from ai_system.graph.state import ClaimAudit, ClaimIssue
from ai_system.research.models import ResearchConflict
from ai_system.research.review_cache import ReviewSuperseded, run_review
from services.chat_services import paper_project as project, review_tasks, issue_edits
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


def test_late_content_review_cannot_pass_a_new_source_version(native):
    async def perform():
        (native.store.root/'part.tex').write_text('用户在检查期间修改的正文')
        return ClaimAudit(issues=[])
    with pytest.raises(ReviewSuperseded):
        asyncio.run(run_review(native.ctx,native.state,'sections',ClaimAudit,'s','p',perform,sections=native.state['sections']))
    assert not any(row['state']=='completed' for row in review_tasks.records(native.store))


@pytest.mark.parametrize('race',[False,True])
def test_native_writer_uses_source_changes_and_retains_user_edits(native,monkeypatch,race):
    store=native.store;before=store.snapshot()['sections']
    async def noop(*args):pass
    ctx=SimpleNamespace(**native.ctx.__dict__,writer_llm=object(),template_requirements='',compact_paper=False,
        set_stage=noop,events=SimpleNamespace(file_changed=noop))
    async def reply(llm,schema,messages,validate):
        assert '过期数据库正文' not in messages[-1].content
        assert '当前原文' in messages[-1].content
        answer=latex.SourceEdits(edits=[latex.SourceEdit(target='part.tex#1',replacement='新正文：结果 {{R:R1}} 有局限。')])
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


def test_duplicate_passages_bind_by_range_and_unlocated_findings_stay_file_scoped(native,monkeypatch):
    from ai_system.modules.writing import consistency
    (native.store.root/'part.tex').write_text('重复原文。\n\n重复原文。\n\n')
    state=review_tasks.paper_state(native.store)
    calls=[]
    async def reply(llm,schema,messages,validate=None,**kwargs):
        calls.append(1)
        assert 'source_issue_id' not in schema.model_fields['issues'].annotation.__args__[0].model_fields
        result=ClaimAudit(issues=[ClaimIssue(section='part.tex',targets=['part.tex#2'],problem='第二段范围错误'),
                                  ClaimIssue(section='part.tex',problem='文件整体缺少适用范围')])
        validate(result);return result
    monkeypatch.setattr(consistency,'call_structured',reply)
    reports=asyncio.run(consistency.audit_sections(state,native.ctx,force=True))
    assert reports==asyncio.run(consistency.audit_sections(state,native.ctx,force=True)) and len(calls)==1
    recorded=native.store.report_issues(reports,'sections')
    issue_edits.bind_findings(native.store,[i['id'] for i in recorded],state['sections'])
    anchored=issue_edits.context(native.store,recorded[0]['id'])
    assert anchored['selection']['start']==len('重复原文。\n\n')
    assert anchored['quote']=='重复原文。\n\n'
    assert issue_edits.context(native.store,recorded[1]['id'])['selection'] is None
    async def noop(*args):pass
    ctx=SimpleNamespace(**native.ctx.__dict__,writer_llm=object(),template_requirements='',compact_paper=False,
                        set_stage=noop,events=SimpleNamespace(file_changed=noop))
    async def replace(llm,schema,messages,validate):
        result=latex.SourceEdits(edits=[latex.SourceEdit(target='part.tex#2',replacement='仅修正第二段。\n\n')])
        validate(result);return result
    monkeypatch.setattr(latex,'call_structured',replace)
    asyncio.run(latex.write(state,SimpleNamespace(context=ctx)))
    assert (native.store.root/'part.tex').read_text()=='重复原文。\n\n仅修正第二段。\n\n'


def test_compile_diagnostic_routes_to_existing_repair_and_only_compiler_closes_it(native,monkeypatch):
    from ai_system.export.latex import LatexResult, BUILD_DIR, compile_diagnostics
    from ai_system.modules.writing.export import _latex
    log='./part.tex:12: Undefined control sequence.\nl.12 结果如\\cref\n {tab:comparison}所示。\n'
    diagnostics=compile_diagnostics(log)
    assert diagnostics[0]['file']=='part.tex' and diagnostics[0]['line']==12
    async def fail(folder,*args,**kwargs):
        build=folder/BUILD_DIR;build.mkdir(parents=True)
        (build/'paper.log').write_text(log)
        return LatexResult('paper.tex',error='part.tex 第12行：Undefined control sequence. \\cref',diagnostics=diagnostics)
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',fail)
    async def noop(*args):pass
    ctx=SimpleNamespace(research=native.store,workspace=native.store.root,set_stage=noop,
                        events=SimpleNamespace(file_changed=noop,emit=noop,stage=noop))
    result=asyncio.run(_latex(ctx))
    assert not result.get('research_blocked') and result['issues']
    issue=next(i for i in native.store.snapshot()['feedback'] if i['source']=='latex.compile')
    assert issue['status']=='confirmed' and not issue['blocking']
    compilation=project.meta(native.store.root)['compile']
    assert (native.store.root/compilation['log_path']).read_text()==log
    assert compilation['diagnostics'][0]['line']==12
    (native.store.root/'part.tex').write_text('修正后的源码')
    assert next(i for i in native.store.snapshot()['feedback'] if i['id']==issue['id'])['status']=='confirmed'
    async def success(folder,*args,**kwargs):
        (folder/'paper.pdf').write_bytes(b'%PDF-verified-fixture')
        return LatexResult('paper.tex',pdf_path='paper.pdf')
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',success)
    asyncio.run(project.compile_project(native.store))
    assert next(i for i in native.store.snapshot()['feedback'] if i['id']==issue['id'])['status']=='resolved'


def test_compile_repairs_stop_after_two_and_resume_writing_only(native,monkeypatch):
    from ai_system.export.latex import LatexResult,BUILD_DIR
    from ai_system.graph.context import GraphContext
    from ai_system.research import loop
    from ai_system.research.control import refresh_registry
    store=native.store;ctx=GraphContext.create(str(store.root),planner_llm=None,output_mode='latex');ctx.research=store
    refresh_registry(ctx)
    before=store.snapshot();calls=[]
    async def broken(folder,*args,**kwargs):
        build=folder/BUILD_DIR;build.mkdir(parents=True,exist_ok=True)
        (build/'paper.log').write_text('! Emergency stop.\n<*> paper.tex\n*** no legal \\end found')
        return LatexResult('paper.tex',error='Emergency stop: no legal \\end found')
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',broken)
    async def reply(llm,schema,messages,validate,**kwargs):
        prompt='\n'.join(m.content for m in messages)
        assert 'Emergency stop' in prompt and '完整日志' in prompt and '当前项目源码片段' in prompt
        calls.append(1)
        result=schema(edits=[{'target':'part.tex','replacement':f'已核对全文，第{len(calls)}次修订。\n'}])
        validate(result);return result
    monkeypatch.setattr(latex,'call_structured',reply)
    with store._transaction() as db:
        state=store._read(db)
        state['publication']={'stage':'export','repair_inputs':[],'repairs':0}
        state['outcome']=None
        store._save(db,state,'test_compile_recovery',{})
    async def advance():
        for _ in range(20):
            if store.snapshot()['outcome']=='completed':return
            if await loop.prepare_action(ctx):return
            action=store.snapshot()['action']
            await loop._act({'action_id':action['id']},SimpleNamespace(context=ctx))
        pytest.fail('publication did not stop')
    asyncio.run(advance())
    stopped=store.snapshot()
    assert len(calls)==2 and stopped['action']['status']=='failed'
    assert stopped['publication']['stage']=='repair' and '两次' in stopped['action']['message']
    assert stopped['outcome']!='completed'
    assert stopped['executions']==before['executions'] and stopped['artifacts']==before['artifacts']
    loop.resume(store)
    assert store.snapshot()['action']['capability']=='writing.repair'
    asyncio.run(advance())
    assert len(calls)==4 and store.snapshot()['publication']['stage']=='repair'
    assert store.snapshot()['executions']==before['executions']
    async def success(folder,*args,**kwargs):
        (folder/'paper.pdf').write_bytes(b'%PDF-repaired')
        return LatexResult('paper.tex',pdf_path='paper.pdf')
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',success)
    loop.resume(store)
    asyncio.run(advance())
    assert store.snapshot()['outcome']=='completed'
    assert project.meta(store.root)['compile']['success']
    assert store.snapshot()['executions']==before['executions'] and store.snapshot()['artifacts']==before['artifacts']
