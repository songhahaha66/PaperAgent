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
    cache.complete(record,ClaimAudit(issues=[ClaimIssue(section='part.tex',targets=['part.tex#1'],problem=issue.message,contradicts=True)]).model_dump())
    issue_edits.bind_findings(native.store,ids,state['sections'])
    current=issue_edits.context(native.store,ids[0])
    assert current['selection']['file']=='part.tex' and current['quote']==state['sections']['part.tex']
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


def test_check_status_uses_source_version_without_requiring_merge(native,monkeypatch):
    async def reply(llm,schema,messages,validate=None,**kwargs):
        answer=ClaimAudit(issues=[])
        if validate:validate(answer)
        return answer
    monkeypatch.setattr('ai_system.modules.writing.consistency.call_structured',reply)
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
    ctx=SimpleNamespace(**native.ctx.__dict__,writer_llm=object(),template_requirements='',
                        set_stage=noop,events=SimpleNamespace(file_changed=noop))
    async def replace(llm,schema,messages,validate):
        result=latex.SourceEdits(edits=[latex.SourceEdit(target='part.tex#2',replacement='仅修正第二段。\n\n')])
        validate(result);return result
    monkeypatch.setattr(latex,'call_structured',replace)
    asyncio.run(latex.write(state,SimpleNamespace(context=ctx)))
    assert (native.store.root/'part.tex').read_text()=='重复原文。\n\n仅修正第二段。\n\n'


def test_review_keeps_old_finding_until_owner_applies_source_edit_and_accepts_advice(native,monkeypatch):
    from ai_system.modules.writing import consistency
    from ai_system.graph.state import SectionIssueReview
    old=native.store.report_issues([IssueReport(task_id='Q1',category='writing',section='part.tex',message='需要限定结论')],'sections')[0]
    async def reply(llm,schema,messages,validate=None,**kwargs):
        result=ClaimAudit(issues=[],issue_reviews=[SectionIssueReview(issue_id=old['id'],verdict='writing',reason='仍未限定范围',
            finding=ClaimIssue(section='part.tex',targets=['part.tex#1'],problem='应说明结论适用范围',contradicts=False))])
        validate(result);return result
    monkeypatch.setattr(consistency,'call_structured',reply)
    reports=asyncio.run(consistency.audit_sections(native.state,native.ctx))
    assert len(reports)==1 and '适用范围' in reports[0].message
    before=native.store.snapshot()
    saved=before['feedback']
    assert next(i for i in saved if i['id']==old['id'])['status']=='confirmed'
    assert len(saved)==1, 'a semantic check returns advice without creating or closing findings'
    [replacement]=native.store.report_issues(reports,'sections',close_writing=False)
    issue_edits.bind_findings(native.store,[replacement['id']],native.state['sections'])
    selected=issue_edits.context(native.store,replacement['id'])['selection']
    assert selected
    # Adoption is an explicit owner operation after the actual source correction.
    current=native.ctx.registry.get('R1')
    project.initialize(native.store)
    change=project.propose_change(native.store,None,None,[{**selected,
        'replacement':'当前原文：本次结果为'+current.value+'，仅限给定样本与条件。\n% paperagent:evidence R1\n'}])
    asyncio.run(project.decide(native.store,change['id'],'apply',compile_after=False))
    from ai_system.research.feedback import revise_interpretation,interpretation_version,FeedbackDecision
    state=native.store.snapshot();task=state['experiments'][old['task_id']]
    revise_interpretation(native.store,task['task_id'],interpretation_version(state,task),decisions=[
        FeedbackDecision(issue_id=i['id'],disposition='adopt',reason='已在当前源码明确给定样本与条件，没有改动计算。',evidence_refs=['R1'])
        for i in (old,replacement)])
    after=native.store.snapshot()
    assert all(next(i for i in after['feedback'] if i['id']==item['id'])['status']=='resolved' for item in (old,replacement))
    assert after['executions']==before['executions'] and after['artifacts']==before['artifacts']


def test_review_failure_retains_actual_reason_and_blocked_action_does_not_repeat_intent(native,monkeypatch):
    from ai_system.modules.writing import consistency
    from ai_system.research.planner import decision_block_message
    async def fail(*args,**kwargs):
        try:raise ValueError('targets 引用了不存在的片段 paper.tex#999')
        except ValueError as exc:raise RuntimeError('结构化输出 ClaimAudit 失败') from exc
    monkeypatch.setattr(consistency,'call_structured',fail)
    asyncio.run(consistency.audit_sections(native.state,native.ctx,force=True))
    failed=next(r for r in review_tasks.records(native.store) if r['state']=='failed')
    assert failed['diagnostic']['reason']=='targets 引用了不存在的片段 paper.tex#999'
    assert 'paper.tex#999' in next(i for i in native.store.snapshot()['faults'] if i['category']=='service')['message']
    snapshot={'action':{'capability':'writing.validate','status':'failed','reason':'准备开始撰写论文',
                        'message':'正文审查未完成：targets 引用错误'},
              'delivery_stages':{'validate':{'note':'正文审查未完成：targets 引用错误'}}}
    assert decision_block_message(None,snapshot)=='正文审查未完成：targets 引用错误'


def test_compile_diagnostic_routes_to_existing_repair_and_only_compiler_closes_it(native,monkeypatch):
    from ai_system.export.latex import LatexResult, BUILD_DIR, compile_diagnostics
    from ai_system.modules.writing.export import _latex
    log='./part.tex:12: Undefined control sequence.\nl.12 结果如\\cref\n {tab:comparison}所示。\n'
    diagnostics=compile_diagnostics(log)
    assert diagnostics[0]['file']=='part.tex' and diagnostics[0]['line']==12
    async def fail(folder,*args):
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
    async def success(folder,*args):
        (folder/'paper.pdf').write_bytes(b'%PDF-verified-fixture')
        return LatexResult('paper.tex',pdf_path='paper.pdf')
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',success)
    asyncio.run(project.compile_project(native.store))
    assert next(i for i in native.store.snapshot()['feedback'] if i['id']==issue['id'])['status']=='resolved'


def test_compile_gate_keeps_first_fatal_context_and_last_success(native,monkeypatch):
    from ai_system.export import latex as compiler
    root=native.store.root
    (root/'paper.pdf').write_bytes(b'last successful PDF')
    previous=project.snapshot(root)['version']
    project.atomic_text(root/project.META,json.dumps({'main':'paper.tex','pdf_version':previous}))
    source=(root/'paper.tex').read_text().replace(r'\end{document}','')
    (root/'paper.tex').write_text(source)
    failed=asyncio.run(project.compile_project(native.store))['compile']
    assert not failed['success'] and '缺少 \\end{document}' in failed['error_summary']
    assert failed['diagnostics'][0]['line'] is None
    assert '不能仅补文档结尾' in (root/failed['log_path']).read_text()
    assert (root/'paper.tex').read_text()==source
    # A fatal error without a location still carries its cause, not the log tail.
    log='! Emergency stop.\n<*> paper.tex\n\n*** (job aborted, no legal \\end found)\n'+'memory stats\n'*100
    assert 'no legal \\end found' in compiler.compile_diagnostics(log)[0]['context']
    (root/'paper.tex').write_text(source+r'\end{document}')
    monkeypatch.setattr(compiler.shutil,'which',lambda _: '/controlled/xelatex')
    async def fail(cmd,cwd,**kwargs):
        (cwd/compiler.BUILD_DIR/'paper.log').write_text(log)
        (cwd/compiler.BUILD_DIR/'paper.pdf').write_bytes(b'partial PDF')
        return 1,log,''
    monkeypatch.setattr(compiler,'run_command',fail)
    failed=asyncio.run(project.compile_project(native.store))['compile']
    assert not failed['success'] and failed['exit_code']==1
    assert 'no legal \\end found' in failed['error_summary']
    assert 'memory stats' in (root/failed['log_path']).read_text()
    assert (root/'paper.pdf').read_bytes()==b'last successful PDF'
    assert project.meta(root)['pdf_version']==previous


def test_compile_gate_checks_final_pass_refs_without_rewriting(native,monkeypatch):
    from ai_system.export import latex as compiler
    root=native.store.root
    source=r'\documentclass{article}\begin{document}See \ref{fig:actual}.\label{fig:actual}\end{document}'
    (root/'paper.tex').write_text(source)
    monkeypatch.setattr(compiler.shutil,'which',lambda _: '/controlled/xelatex')
    calls=[]
    async def passes(cmd,cwd,**kwargs):
        calls.append(cmd)
        log="LaTeX Warning: Reference `fig:actual' on page 1 undefined.\n" if len(calls)==1 else 'Output written on paper.pdf (1 page).'
        (cwd/compiler.BUILD_DIR/'paper.log').write_text(log)
        (cwd/compiler.BUILD_DIR/'paper.pdf').write_bytes(b'checked PDF')
        return 0,log,''
    monkeypatch.setattr(compiler,'run_command',passes)
    result=asyncio.run(compiler.compile_pdf(root))
    assert result.success and len(calls)==3 and (root/'paper.tex').read_text()==source
    async def undefined(cmd,cwd,**kwargs):
        (cwd/compiler.BUILD_DIR/'paper.log').write_text("LaTeX Warning: Reference `fig:missing' on page 1 undefined.\n")
        (cwd/compiler.BUILD_DIR/'paper.pdf').write_bytes(b'unchecked PDF')
        return 0,'',''
    monkeypatch.setattr(compiler,'run_command',undefined)
    result=asyncio.run(compiler.compile_pdf(root))
    assert not result.success and result.exit_code==0 and '未定义' in result.error
    assert (root/'paper.pdf').read_bytes()==b'checked PDF'
    (root/'paper.tex').write_text(source.replace(r'\ref{fig:actual}',r'\ref{R:R25}'))
    result=asyncio.run(compiler.compile_pdf(root))
    assert not result.success and '登记结果 ID' in result.error
    assert r'\ref{R:R25}' in (root/'paper.tex').read_text()


def test_compile_repairs_stop_after_two_and_resume_writing_only(native,monkeypatch):
    from ai_system.export.latex import LatexResult,BUILD_DIR
    from ai_system.graph.context import GraphContext
    from ai_system.research import loop
    from ai_system.research.control import refresh_registry
    store=native.store;ctx=GraphContext.create(str(store.root),planner_llm=None,output_mode='latex');ctx.research=store
    refresh_registry(ctx)
    before=store.snapshot();calls=[]
    async def broken(folder,*args):
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
    async def success(folder,*args):
        (folder/'paper.pdf').write_bytes(b'%PDF-repaired')
        return LatexResult('paper.tex',pdf_path='paper.pdf')
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',success)
    loop.resume(store)
    asyncio.run(advance())
    assert store.snapshot()['outcome']=='completed'
    assert project.meta(store.root)['compile']['success']
    assert store.snapshot()['executions']==before['executions'] and store.snapshot()['artifacts']==before['artifacts']


def test_writer_reports_missing_results_and_records_value_provenance(native,monkeypatch):
    from ai_system.graph.context import GraphContext
    from ai_system.research.control import graph_view
    ctx=GraphContext.create(str(native.store.root),planner_llm=None,output_mode='latex');ctx.research=native.store
    state=graph_view(native.store.snapshot())
    async def missing(llm,schema,messages,validate,**kwargs):
        with pytest.raises(ValueError,match='R999'):
            validate(schema(edits=[{'target':'part.tex','replacement':'未知结果','result_refs':['R999']}]))
        return schema(research_gap='R999 未登记，无法确认实际数值和适用范围')
    monkeypatch.setattr(latex,'call_structured',missing)
    result=asyncio.run(latex.write(state,SimpleNamespace(context=ctx)))
    assert result['research_blocked'] and 'R999' in result['message'] and not result.get('research_restart')
    async def actual(llm,schema,messages,validate,**kwargs):
        result=schema(edits=[{'target':'part.tex','replacement':r'本次输入下需求为 8，见\ref{sec:methods}。','result_refs':['R1']}])
        validate(result);return result
    monkeypatch.setattr(latex,'call_structured',actual)
    asyncio.run(latex.write(state,SimpleNamespace(context=ctx)))
    source=(native.store.root/'part.tex').read_text()
    assert '本次输入下需求为 8' in source and r'\ref{sec:methods}' in source
    assert '% paperagent:evidence R1' in source
