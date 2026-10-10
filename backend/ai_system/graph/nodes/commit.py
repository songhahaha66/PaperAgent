from __future__ import annotations

import json
from pathlib import Path

from ...schemas.draft import Draft
from ...schemas.paper_ir import SectionContent
from ..state import PaperState

IR_PATH = Path(".system") / "paper_ir.json"


def commit_drafts(state: PaperState, drafts: list[Draft]) -> PaperState:
    if state.ir is None:
        from ...schemas.paper_ir import PaperIR

        state.ir = PaperIR(work_id=state.work_id)
    for draft in drafts:
        previous = state.ir.sections.get(draft.slot_id)
        revision = (previous.revision + 1) if previous else 1
        state.ir.sections[draft.slot_id] = SectionContent(
            slot_id=draft.slot_id,
            blocks=draft.blocks,
            provenance=draft.provenance,
            revision=revision,
        )
        state.ir.revision += 1
        state.slot_repairs.setdefault(draft.slot_id, 0)
    path = Path(state.workspace_dir) / IR_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.ir.model_dump_json(indent=2), encoding="utf-8")
    return state
