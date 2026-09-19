from __future__ import annotations

from pathlib import Path

from ...validate.runner import validate_document
from ..state import PaperState


def validate_node(state: PaperState) -> PaperState:
    if state.spec is None or state.ir is None or not state.rendered_path:
        return state
    template = None
    workspace = Path(state.workspace_dir)
    candidate = workspace / ".system" / "_template_original.docx"
    if candidate.exists():
        template = candidate
    state.issues = validate_document(state.spec, state.ir, state.rendered_path, template)
    return state
