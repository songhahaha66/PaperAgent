"""
一次交付当前稿件：检查源文件、编译或转换、保存导出回执。

- latex：直接编译已发布的 paper.tex 为 paper.pdf；
- word：paper.docx，用户上传了 Word 模板时沿用模板的样式。

编译或导出失败时保留源文件，并将交付阶段标记为阻塞。
"""

import hashlib
from langgraph.runtime import Runtime

from ai_system.export import export_docx
from ai_system.graph.context import TEMPLATE_DOCX, GraphContext
from ai_system.graph.state import PaperState


async def _latex(ctx: GraphContext) -> dict:
    from services.chat_services.paper_project import compile_project, source_state
    from ai_system.research.views import activity
    with activity(ctx,'writing.compile') as operation:
        data=await compile_project(ctx.research)
        operation['display_file']='paper.pdf' if data['compile']['success'] else 'paper.tex'
        if not data['compile']['success']:
            operation.update(status='failed',error_type='LatexCompileError',message=data['compile']['message'])
    compilation=data['compile']
    await ctx.events.file_changed('paper.tex')
    if compilation['success']:
        pages = f"（{compilation['pages']} 页）" if compilation['pages'] else ""
        await ctx.events.file_changed('paper.pdf')
        note = f"LaTeX 排版完成：paper.tex，编译出 paper.pdf{pages}"
        if compilation['warnings']:
            note += "\n\n编译警告：" + "；".join(compilation['warnings'])
        await ctx.events.emit("paper_export", note)
        return {"exports":['paper.tex','paper.pdf'],"source_version":compilation["source_version"],"log": ["export: paper.pdf"]}

    error=compilation['message']
    await ctx.set_stage("export", "blocked", error)
    await ctx.events.emit("paper_export", f"论文源码已保存，PDF 编译未完成：{error}")
    from ai_system.research.models import IssueReport
    source=source_state(ctx.research,{})
    diagnostics=compilation['diagnostics'] or [{'file':None,'line':None,'message':error,'context':''}]
    if not ctx.research.snapshot()['experiments']:
        return {'issues':[{'section':'paper.tex','message':error}],'exports':['paper.tex'],'log':[compilation['log_path']]}
    if compilation['source_version']==source['source_version']:
        task=next(iter(ctx.research.snapshot()['experiments']))
        reports=[IssueReport(task_id=task,category='writing',section=d['file'] if d['file'] in source['sections'] else data.get('main','paper.tex'),
            message='LaTeX 编译未通过'+(f"，第{d['line']}行" if d['line'] is not None else '')+
                f"：{d['message']}\n{d['context']}\n完整日志：{compilation['log_path']}") for d in diagnostics]
        with ctx.research._transaction() as db:
            snapshot=ctx.research._read(db)
            for old in snapshot['feedback']:
                if old['source']=='latex.compile' and old['status'] not in {'resolved','dismissed','superseded'}:
                    old['status']='superseded'
            issues=[ctx.research._record_issue(snapshot,report,'confirmed',source='latex.compile') for report in reports]
            # The compiler is the gate for these findings. Keep rendering
            # reachable after repair; only a successful compile closes them.
            for issue in issues:issue['blocking']=False
            ctx.research._save(db,snapshot,'latex_source_errors',{'issues':[i['id'] for i in issues]})
        from services.chat_services.issue_edits import bind_findings
        bind_findings(ctx.research,[i['id'] for i in issues],source['sections'],source_version=source['source_version'])
        await ctx.events.stage('源码编译未通过，诊断已交回研究 Agent')
        return {'issues':issues,'exports':['paper.tex'],'log':[f'export: {error}; repairing source']}
    return {"research_blocked": True, "exports":['paper.tex'],"message":error, "log": [f"export: {error}"]}


async def _word(ctx: GraphContext, markdown: str) -> dict:
    template = ctx.workspace / TEMPLATE_DOCX
    result = await export_docx(ctx.workspace, markdown, template_docx=template if template.exists() else None, source_version=hashlib.sha256(markdown.encode()).hexdigest())
    await ctx.events.file_changed(result.docx_path)
    style = "沿用上传模板的样式" if result.reference == "template" else "默认论文样式"
    await ctx.events.emit("paper_export", f"已导出 {result.docx_path}（{style}）")
    return {"exports":[result.docx_path],"log": [f"export: {result.docx_path} ({result.reference})"]}


def prepare_source(state: PaperState, ctx: GraphContext) -> dict:
    """Local file/reference checks only; no review model or answer-registration gate."""
    from .validation import check_sections, assemble
    from ai_system.graph.registry import REF_PATTERN, publication_text
    from ai_system.research.citations import render_citations, literature_root
    from services.chat_services.paper_project import source_state, snapshot as source_snapshot
    native=ctx.output_mode=='latex'
    name='paper.tex' if native else 'paper.md'
    if native:
        state=source_state(ctx.research,state)
        raw=(ctx.workspace/name).read_text(encoding='utf-8')
        version=state['source_version']
    else:
        path=ctx.workspace/name
        raw=path.read_text(encoding='utf-8') if path.is_file() else assemble(state)
        version=hashlib.sha256(raw.encode()).hexdigest()
        state={**state,'document':{'path':name},'sections':{'document':raw}}
    if not raw.strip():raise ValueError('正文为空，请先写入当前稿件')
    issues=check_sections(state,ctx)
    if issues:return {'issues':issues}
    if native:
        from services.chat_services.paper_sources import validate_references
        for text in state['sections'].values():validate_references(ctx.workspace,text,text)
        if version!=source_snapshot(ctx.workspace)['version']:raise ValueError('源码已改变，请导出当前版本')
        rendered=raw
    else:
        rendered=publication_text(render_citations(literature_root(ctx),ctx.registry.render(raw),False))
    if REF_PATTERN.search(rendered):raise ValueError('正文仍有未解析的历史结果引用')
    if native:
        path,conflict=name,False
    else:
        # Compare and publish under the edit lock so a concurrent edit is preserved.
        from services.file_services.workspace_lock import edit_lock
        with edit_lock(ctx.workspace):
            active=ctx.workspace/name
            if active.exists() and hashlib.sha256(active.read_bytes()).hexdigest()!=version:
                raise ValueError('正文在导出期间改变，请读取当前版本')
            path,conflict=ctx.research.publish_paper(rendered,filename=name)
        version=hashlib.sha256(rendered.encode()).hexdigest()
    ctx.fs.register_file(path,kind='document',created_by='ai',visibility='user')
    return {'paper_path':path,'artifact_conflict':conflict,'source_version':version}


async def export(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx=runtime.context
    await ctx.set_stage('export','in_progress')
    try:
        from ai_system.research.runs import direct_runs
        pending=[rid for rid,r in direct_runs(ctx.research,ctx.fs).items()
                 if r.get('delivery_error') or r.get('publication_error') or r.get('status') in {'starting','running'}]
        if pending:raise ValueError('运行或文件交付尚未完成：'+', '.join(pending))
        prepared=prepare_source(state,ctx)
        if prepared.get('issues'):
            await ctx.set_stage('export','blocked','正文文件或引用不可用，请修正后导出')
            return prepared
        await ctx.events.file_changed(prepared['paper_path'])
        if prepared['artifact_conflict']:
            await ctx.set_stage('export','blocked','正文有人工修改，已保留两份稿件，等待合并')
            return prepared
        if ctx.output_mode=='latex':
            result=await _latex(ctx)
        elif ctx.output_mode=='word':
            text=(ctx.workspace/'paper.md').read_text(encoding='utf-8')
            if hashlib.sha256(text.encode()).hexdigest()!=prepared['source_version']:
                raise ValueError('正文在导出期间改变，请导出当前版本')
            result=await _word(ctx,text)
        else:
            result={'exports':['paper.md'],'log':['export: paper.md']}
        paths=result.pop('exports')
        version=result.pop('source_version',prepared['source_version'])
        ctx.research.register_exports(paths,source_version=version if not result.get('issues') and not result.get('research_blocked') else None)
        if not result.get('issues') and not result.get('research_blocked'):
            await ctx.set_stage('export','completed',paths[-1] if paths else prepared['paper_path'])
        return {**prepared,**result}
    except Exception as exc:
        await ctx.set_stage("export", "blocked", str(exc)[:80])
        await ctx.events.emit("paper_export", f"导出失败：{exc}")
        if isinstance(exc,(ValueError,KeyError,FileNotFoundError)):
            raise  # The Agent can correct tool inputs/files in its current conversation.
        return {"research_blocked": True, "message":f'导出失败：{exc}', "log": [f"export: failed ({exc})"]}
