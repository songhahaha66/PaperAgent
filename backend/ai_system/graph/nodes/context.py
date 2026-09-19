from __future__ import annotations

import json
from pathlib import Path

from ...schemas.paper_ir import PaperIR
from ...template.spec_builder import load_template_spec
from ..checkpoint import load_checkpoint
from ..state import PaperState

IR_PATH = Path(".system") / "paper_ir.json"
SPEC_PATH = Path(".system") / "template_spec.json"


def load_context(state: PaperState) -> PaperState:
    workspace = Path(state.workspace_dir)
    previous = load_checkpoint(workspace)
    if previous and previous.work_id == state.work_id:
        previous.user_message = state.user_message
        previous.run_id = state.run_id or previous.run_id
        previous.finished = False
        previous.awaiting_confirmation = False
        previous.issues = []
        previous.messages = []
        previous.summary = ""
        state = previous

    ir_file = workspace / IR_PATH
    if state.ir is None and ir_file.exists():
        try:
            state.ir = PaperIR.model_validate(json.loads(ir_file.read_text(encoding="utf-8")))
        except Exception:
            state.ir = None
    if state.ir is None:
        state.ir = PaperIR(work_id=state.work_id)

    spec_file = workspace / SPEC_PATH
    if state.spec is None and not spec_file.exists():
        spec_file = workspace / ".system" / "spec.json"
    if state.spec is None and spec_file.exists():
        try:
            from ...schemas.template_spec import TemplateSpec

            state.spec = TemplateSpec.model_validate(json.loads(spec_file.read_text(encoding="utf-8")))
        except Exception:
            state.spec = None
    if state.spec is None and state.template_id:
        state.spec = load_template_spec(state.template_id)
    return state
