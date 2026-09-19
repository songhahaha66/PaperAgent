from __future__ import annotations

import logging
from pathlib import Path

from ...schemas.paper_ir import Artifact
from ...schemas.template_spec import Slot
from ..state import PaperState

logger = logging.getLogger(__name__)


def needed_gather_slots(state: PaperState, slot_ids: list[str]) -> list[Slot]:
    if state.spec is None:
        return []
    return [
        slot
        for slot in state.spec.slots
        if slot.id in slot_ids and slot.role == "figure_slot"
    ]


async def gather_batch(state: PaperState, slot_ids: list[str], gather_fn=None, coder_llm=None, stream=None) -> PaperState:
    if state.spec is None or state.ir is None:
        return state
    needed = needed_gather_slots(state, slot_ids)
    if not needed:
        return state
    if gather_fn:
        result = gather_fn(state, needed)
        if hasattr(result, "__await__"):
            await result
        return state
    if coder_llm is not None:
        return await _gather_with_code_agent(state, needed, coder_llm, stream)
    return state


async def _gather_with_code_agent(state: PaperState, needed: list[Slot], coder_llm, stream) -> PaperState:
    try:
        from ...core_agents.code_agent import CodeAgent

        agent = CodeAgent(
            llm=coder_llm,
            stream_manager=stream,
            workspace_dir=state.workspace_dir,
            work_id=state.work_id,
        )
        titles = "、".join(slot.title or slot.id for slot in needed)
        await agent.run(
            f"为论文槽位准备图表或数据产物：{titles}\n"
            f"用户需求：{state.user_message}\n"
            "把生成的图片或数据文件保存到 outputs/ 目录。"
        )
    except Exception as exc:
        logger.warning("CodeAgent gather 失败，跳过本批产物: %s", exc)
        return state
    _register_output_artifacts(state)
    return state


def _register_output_artifacts(state: PaperState) -> None:
    if state.ir is None:
        return
    outputs = Path(state.workspace_dir) / "outputs"
    if not outputs.exists():
        return
    for path in outputs.iterdir():
        if not path.is_file() or path.name.startswith("_"):
            continue
        state.ir.artifacts[path.stem] = Artifact(
            id=path.stem,
            path=str(Path("outputs") / path.name),
            mime=_mime_for(path.suffix),
            produced_by="code_agent",
        )


def _mime_for(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".csv": "text/csv",
        ".txt": "text/plain",
    }.get(suffix.lower(), "application/octet-stream")
