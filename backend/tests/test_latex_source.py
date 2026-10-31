"""Current-source contracts; controlled models only, no API or full application run."""
import asyncio
import subprocess
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
from ai_system.modules.writing import draft


def test_graphicspath_assets_are_included_in_the_immutable_source_snapshot(tmp_path):
    (tmp_path/'results/figures').mkdir(parents=True)
    (tmp_path/'results/figures/curve.pdf').write_bytes(b'original curve')
    (tmp_path/'results/figures/other.png').write_bytes(b'unreferenced')
    (tmp_path/'paper.tex').write_text(r'\graphicspath{{./}{results/figures/}}\input{part}')
    (tmp_path/'part.tex').write_text(r'\includegraphics[width=5cm]{curve}')
    before=project.snapshot(tmp_path)
    assert {row['path'] for row in before['files']}=={'paper.tex','part.tex','results/figures/curve.pdf'}
    archive=project.workspace_path(tmp_path,project.VERSIONS)/before['version']
    assert (archive/'results/figures/curve.pdf').read_bytes()==b'original curve'
    (tmp_path/'results/figures/curve.pdf').write_bytes(b'changed curve')
    assert project.snapshot(tmp_path)['version']!=before['version']


def test_graphicspath_handles_whitespace_and_multiple_directories(tmp_path):
    (tmp_path/'figures').mkdir()
    (tmp_path/'figures/curve.png').write_bytes(b'figure')
    (tmp_path/'paper.tex').write_text('\\graphicspath {  {unused/} \n {figures/}  }\\includegraphics{curve}')
    assert 'figures/curve.png' in project.files(tmp_path)


def test_malformed_graphicspath_does_not_block_dependency_parsing(tmp_path):
    (tmp_path/'figures').mkdir()
    (tmp_path/'figures/curve.png').write_bytes(b'figure')
    (tmp_path/'paper.tex').write_text(
        '\\graphicspath{' + ' {unused/}' * 5000 + '\n'
        + r'\graphicspath{{figures/}}\includegraphics{curve}'
    )
    # A subprocess timeout prevents a regression from hanging the test runner.
    script = ('from pathlib import Path; import sys; '
              'from services.chat_services.paper_project import files; '
              "assert 'figures/curve.png' in files(Path(sys.argv[1]))")
    subprocess.run([sys.executable, '-c', script, str(tmp_path)], check=True, timeout=10)


@pytest.mark.parametrize('directory',['../outside/', '.system/', '/tmp/'])
def test_graphicspath_does_not_expand_the_allowed_source_tree(tmp_path,directory):
    (tmp_path/'paper.tex').write_text(r'\graphicspath{{'+directory+r'}}\includegraphics{curve}')
    with pytest.raises(ValueError):project.files(tmp_path)


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
    from ai_system.graph.context import GraphContext
    ctx=GraphContext.create(str(store.root),planner_llm=None,output_mode='latex');ctx.research=store
    ctx.registry=context_from_store(store,'offline-model').registry
    return SimpleNamespace(store=store,state=review_tasks.paper_state(store),ctx=ctx)


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
    import hashlib
    old=(store.root/'part.tex').read_text()
    version=hashlib.sha256(old.encode()).hexdigest()
    if race:(store.root/'part.tex').write_text('用户在生成期间的新编辑')
    values={'path':'part.tex','content':'新正文：结果8有局限。','expected_version':version,'old_text':None}
    if race:
        with pytest.raises(ResearchConflict):asyncio.run(draft.edit(native.ctx,values))
        assert (store.root/'part.tex').read_text()=='用户在生成期间的新编辑'
    else:
        asyncio.run(draft.edit(native.ctx,values))
        assert '新正文：结果8' in (store.root/'part.tex').read_text()
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
    import hashlib
    old=(native.store.root/'part.tex').read_text()
    asyncio.run(draft.edit(native.ctx,{'path':'part.tex','content':'重复原文。\n\n仅修正第二段。\n\n',
        'expected_version':hashlib.sha256(old.encode()).hexdigest(),'old_text':None}))
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


def test_compile_failure_returns_to_research_without_replaying_calculation(native,monkeypatch):
    from ai_system.research import loop
    from ai_system.graph.context import GraphContext
    store=native.store;ctx=GraphContext.create(str(store.root),planner_llm=None,output_mode='latex');ctx.research=store
    before=store.snapshot()['executions']
    async def failed(ctx):return {'issues':[{'message':'编译错误，读取日志后修正'}],'exports':[]}
    monkeypatch.setattr('ai_system.modules.writing.export._latex',failed)
    with store._transaction() as db:
        state=store._read(db);state['publication']={'stage':'export','repair_inputs':[],'repairs':0};state['outcome']='running'
        action=loop._set_action(state,'writing.export',{},'导出当前源码')
        store._save(db,state,'test_compile_failure',{})
    asyncio.run(loop._act({'action_id':action['id']},SimpleNamespace(context=ctx)))
    assert store.snapshot()['publication'] is None
    assert store.snapshot()['action']['result']['issues']
    assert store.snapshot()['executions']==before
    # A tool exception must also reach the live Agent, with a failed receipt.
    async def unavailable(ctx):raise ValueError('controlled export failure')
    monkeypatch.setattr('ai_system.modules.writing.export._latex',unavailable)
    with store._transaction() as db:
        state=store._read(db);state['publication']={'stage':'export'}
        action=loop._set_action(state,'writing.export',{},'导出当前源码')
        store._save(db,state,'retry_export',{})
    asyncio.run(loop._act({'action_id':action['id']},SimpleNamespace(context=ctx)))
    assert store.snapshot()['action']['status']=='failed'
    assert loop.select({},SimpleNamespace(context=ctx))=='observe'
    assert asyncio.run(loop._act({'action_id':action['id']},SimpleNamespace(context=ctx)))=={}
    loop.resume(store)
    assert store.snapshot()['action']['status']=='failed'
    from langchain_core.messages import ToolMessage
    from research_tools_helpers import ResearchToolsModel,tool
    ctx.fs.register_file('part.tex',kind='document',created_by='ai',visibility='user')
    def respond(messages):
        rows=[m for m in messages if isinstance(m,ToolMessage)]
        assert all(m.status!='error' for m in rows), [m.content for m in rows]
        if not rows:
            assert any('controlled export failure' in str(m.content) for m in messages)
            return tool('research_read_artifact',{'path':'part.tex'},'read')
        if len(rows)==1:
            return tool('writing_edit',{'path':'part.tex','content':'修正后的正文，保留结果与局限。'},'edit')
        return tool('research_deliver',{'title':'修正后的报告'},'deliver')
    ctx.planner_llm=ResearchToolsModel(respond=respond)
    assert not asyncio.run(loop.prepare_action(ctx))
    assert '修正后的正文' in (store.root/'part.tex').read_text()
    assert store.snapshot()['action']['capability']=='writing.validate'
    assert store.snapshot()['executions']==before
