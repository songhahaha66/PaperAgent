"""
阶段 8（output_mode=latex）：paper.md → paper.tex → paper.pdf

排版失败不影响论文本身：paper.md 已经写好，错误记进进度和事件，流程照常结束。
"""

from langgraph.runtime import Runtime

from ...latex import typeset as typeset_latex
from ..context import GraphContext
from ..state import PaperState


def route_after_render(state: PaperState, runtime: Runtime[GraphContext]) -> str:
    return "typeset" if runtime.context.output_mode == "latex" else "__end__"


async def typeset(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    await ctx.set_stage("typeset", "in_progress")
    await ctx.events.stage("LaTeX 排版并编译 PDF")
    markdown = (ctx.workspace / "paper.md").read_text(encoding="utf-8")
    try:
        result = await typeset_latex(ctx.workspace, markdown)
    except Exception as exc:
        await ctx.set_stage("typeset", "blocked", str(exc)[:80])
        await ctx.events.emit("paper_typeset", f"LaTeX 排版失败：{exc}")
        return {"log": [f"typeset: failed ({exc})"]}

    await ctx.events.file_changed(result.tex_path)
    if result.pdf_path:
        pages = f"（{result.pages} 页）" if result.pages else ""
        await ctx.events.file_changed(result.pdf_path)
        await ctx.set_stage("typeset", "completed", f"paper.pdf{pages}")
        note = f"LaTeX 排版完成：{result.tex_path}，编译出 {result.pdf_path}{pages}"
        if result.warnings:
            note += "\n\n编译警告：" + "；".join(result.warnings)
        await ctx.events.emit("paper_typeset", note)
        return {"log": [f"typeset: {result.pdf_path}"]}

    await ctx.set_stage("typeset", "blocked", "只生成了 paper.tex")
    await ctx.events.emit("paper_typeset", f"已生成 {result.tex_path}，PDF 未生成：{result.error}")
    return {"log": [f"typeset: tex only ({result.error})"]}
