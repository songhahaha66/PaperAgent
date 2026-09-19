from __future__ import annotations

from pathlib import Path

from ...render.docx_renderer import render_docx
from ...render.markdown_renderer import render_markdown
from ..state import PaperState


def render_node(state: PaperState) -> PaperState:
    if state.spec is None or state.ir is None:
        return state
    workspace = Path(state.workspace_dir)
    if state.output_mode == "word":
        template = workspace / ".system" / "_template_original.docx"
        if not template.exists():
            template = workspace / "paper.docx"
        output = workspace / "paper.docx"
        if template.exists():
            render_docx(state.spec, state.ir, template, output)
            state.rendered_path = str(output)
        return state
    output = workspace / "paper.md"
    render_markdown(state.spec, state.ir, output)
    state.rendered_path = str(output)
    return state
