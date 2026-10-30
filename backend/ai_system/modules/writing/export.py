"""
阶段 8：直接编译原生 paper.tex，或将 Markdown 导出为 Word

- latex：直接编译已发布的 paper.tex 为 paper.pdf；
- word：paper.docx，用户上传了 Word 模板时沿用模板的样式。

编译或导出失败时保留源文件，并将交付阶段标记为阻塞。
"""

from langgraph.runtime import Runtime

from ai_system.export import export_docx
from ai_system.graph.context import TEMPLATE_DOCX, GraphContext
from ai_system.graph.state import PaperState

EXPORT_MODES = ("latex", "word")


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
        await ctx.set_stage("export", "completed", f"paper.pdf{pages}")
        note = f"LaTeX 排版完成：paper.tex，编译出 paper.pdf{pages}"
        if compilation['warnings']:
            note += "\n\n编译警告：" + "；".join(compilation['warnings'])
        await ctx.events.emit("paper_export", note)
        return {"exports":['paper.tex','paper.pdf'],"log": ["export: paper.pdf"]}

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
        await ctx.events.stage('源码编译未通过，正在自动修正后重新检查')
        return {'issues':issues,'exports':['paper.tex'],'log':[f'export: {error}; repairing source']}
    return {"research_blocked": True, "exports":['paper.tex'],"message":error, "log": [f"export: {error}"]}


async def _word(ctx: GraphContext, markdown: str) -> dict:
    template = ctx.workspace / TEMPLATE_DOCX
    result = await export_docx(ctx.workspace, markdown, template_docx=template if template.exists() else None)
    await ctx.events.file_changed(result.docx_path)
    style = "沿用上传模板的样式" if result.reference == "template" else "默认论文样式"
    await ctx.set_stage("export", "completed", result.docx_path)
    await ctx.events.emit("paper_export", f"已导出 {result.docx_path}（{style}）")
    return {"exports":[result.docx_path],"log": [f"export: {result.docx_path} ({result.reference})"]}


async def export(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    await ctx.set_stage("export", "in_progress")
    await ctx.events.stage("LaTeX 排版并编译 PDF" if ctx.output_mode == "latex" else "导出 Word 文档")
    try:
        result = await _latex(ctx) if ctx.output_mode == "latex" else await _word(ctx, (ctx.workspace / "paper.md").read_text(encoding="utf-8"))
        ctx.research.register_exports(result.pop('exports'))
        return result
    except Exception as exc:
        await ctx.set_stage("export", "blocked", str(exc)[:80])
        await ctx.events.emit("paper_export", f"导出失败：{exc}")
        return {"research_blocked": True, "message":f'导出失败：{exc}', "log": [f"export: failed ({exc})"]}
