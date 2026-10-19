"""
阶段 8：直接编译原生 paper.tex，或将 Markdown 导出为 Word

- latex：直接编译已发布的 paper.tex 为 paper.pdf；
- word：paper.docx，用户上传了 Word 模板时沿用模板的样式。

编译或导出失败时保留源文件，并将交付阶段标记为阻塞。
"""

from langgraph.runtime import Runtime

from ai_system.export import export_docx, compile_pdf
from ai_system.graph.context import TEMPLATE_DOCX, GraphContext
from ai_system.graph.state import PaperState

EXPORT_MODES = ("latex", "word")


def route_after_render(state: PaperState, runtime: Runtime[GraphContext]) -> str:
    if state.get("research_restart"):
        return "observe"
    if state.get("artifact_conflict"):
        return "__end__"
    return "export" if runtime.context.output_mode in EXPORT_MODES else "__end__"


async def _latex(ctx: GraphContext) -> dict:
    if ctx.research:
        from services.chat_services.paper_project import compile_project, meta
        await compile_project(ctx.research)
        from types import SimpleNamespace
        data=meta(ctx.workspace)
        result=SimpleNamespace(tex_path='paper.tex',pdf_path='paper.pdf' if data.get('compile',{}).get('state')=='current' else None,
                               pages=None,warnings=data.get('compile',{}).get('warnings',[]),error=data.get('compile',{}).get('message'))
    else:
        result = await compile_pdf(ctx.workspace)
    await ctx.events.file_changed(result.tex_path)
    if result.pdf_path:
        pages = f"（{result.pages} 页）" if result.pages else ""
        await ctx.events.file_changed(result.pdf_path)
        await ctx.set_stage("export", "completed", f"paper.pdf{pages}")
        note = f"LaTeX 排版完成：{result.tex_path}，编译出 {result.pdf_path}{pages}"
        if result.warnings:
            note += "\n\n编译警告：" + "；".join(result.warnings)
        await ctx.events.emit("paper_export", note)
        return {"log": [f"export: {result.pdf_path}"]}

    await ctx.set_stage("export", "blocked", result.error or "PDF 未生成")
    await ctx.events.emit("paper_export", f"已生成 {result.tex_path}，PDF 未生成：{result.error}")
    return {"research_blocked": True, "log": [f"export: tex only ({result.error})"]}


async def _word(ctx: GraphContext, markdown: str) -> dict:
    template = ctx.workspace / TEMPLATE_DOCX
    result = await export_docx(ctx.workspace, markdown, template_docx=template if template.exists() else None)
    await ctx.events.file_changed(result.docx_path)
    style = "沿用上传模板的样式" if result.reference == "template" else "默认论文样式"
    await ctx.set_stage("export", "completed", result.docx_path)
    await ctx.events.emit("paper_export", f"已导出 {result.docx_path}（{style}）")
    return {"log": [f"export: {result.docx_path} ({result.reference})"]}


async def export(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    await ctx.set_stage("export", "in_progress")
    await ctx.events.stage("LaTeX 排版并编译 PDF" if ctx.output_mode == "latex" else "导出 Word 文档")
    try:
        result = await _latex(ctx) if ctx.output_mode == "latex" else await _word(ctx, (ctx.workspace / "paper.md").read_text(encoding="utf-8"))
        if ctx.research:
            message = " ".join(result.get("log") or [])
            paths = (["paper.tex"] if "tex only" in message else ["paper.tex", "paper.pdf"]) if ctx.output_mode == "latex" else ["paper.docx"]
            ctx.research.register_exports(paths)
        return result
    except Exception as exc:
        await ctx.set_stage("export", "blocked", str(exc)[:80])
        await ctx.events.emit("paper_export", f"导出失败：{exc}")
        return {"research_blocked": True, "log": [f"export: failed ({exc})"]}
