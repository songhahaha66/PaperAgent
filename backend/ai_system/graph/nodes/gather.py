from __future__ import annotations

from pathlib import Path

from ...sandbox.runner import run_sandbox
from ...schemas.paper_ir import Artifact
from ..state import PaperState


async def gather_batch(state: PaperState, slot_ids: list[str], gather_fn=None) -> PaperState:
    if state.spec is None or state.ir is None:
        return state
    needed = [
        slot
        for slot in state.spec.slots
        if slot.id in slot_ids and (slot.role == "figure_slot" or "code" in slot.expects)
    ]
    if not needed:
        return state
    if gather_fn:
        result = gather_fn(state, needed)
        if hasattr(result, "__await__"):
            await result
        return state
    workdir = Path(state.workspace_dir) / "outputs"
    result = run_sandbox(
        "from pathlib import Path\n"
        "Path('note.txt').write_text('gathered', encoding='utf-8')\n",
        workdir,
        timeout=15,
    )
    for name in result.artifacts:
        artifact_id = Path(name).stem
        state.ir.artifacts[artifact_id] = Artifact(
            id=artifact_id,
            path=str(Path("outputs") / name),
            mime="text/plain",
            produced_by="sandbox",
        )
    return state
