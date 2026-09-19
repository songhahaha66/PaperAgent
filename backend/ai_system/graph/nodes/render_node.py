from __future__ import annotations

import shutil
from pathlib import Path

from ...render.docx_renderer import render_docx
from ...render.markdown_renderer import render_markdown
from ..state import PaperState

TEMPLATE_SNAPSHOT = Path(".system") / "_template_original.docx"


def pristine_template(workspace: Path) -> Path | None:
    """Return the untouched template copy, creating it from paper.docx on first use.

    Rendering must always start from the pristine template; rendering on top of an
    already-filled paper.docx would duplicate content on every run.
    """
    snapshot = workspace / TEMPLATE_SNAPSHOT
    if snapshot.exists():
        return snapshot
    paper = workspace / "paper.docx"
    if paper.exists():
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(paper, snapshot)
        return snapshot
    return None


def render_node(state: PaperState) -> PaperState:
    if state.spec is None or state.ir is None:
        return state
    workspace = Path(state.workspace_dir)
    if state.output_mode == "word":
        output = workspace / "paper.docx"
        render_docx(state.spec, state.ir, pristine_template(workspace), output)
        state.rendered_path = str(output)
        return state
    output = workspace / "paper.md"
    render_markdown(state.spec, state.ir, output)
    state.rendered_path = str(output)
    return state
