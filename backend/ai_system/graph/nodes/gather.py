from __future__ import annotations

import logging
from pathlib import Path

from ...schemas.paper_ir import Artifact
from ...schemas.template_spec import Slot
from ..state import PaperState

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg"}
DATA_SUFFIXES = {".csv", ".txt", ".json"}


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
        register_output_artifacts(state)
        return state
    if coder_llm is not None:
        return await _gather_with_code_agent(state, needed, coder_llm, stream)
    return state


async def _gather_with_code_agent(state: PaperState, needed: list[Slot], coder_llm, stream) -> PaperState:
    known_runs = _run_ids(state.workspace_dir)
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
            "生成的图直接用 plt.show() 输出，数据用 print() 输出，系统会自动收集。"
        )
    except Exception as exc:
        logger.warning("CodeAgent gather 失败，跳过本批产物: %s", exc)
        return state
    promoted = promote_new_run_artifacts(state.workspace_dir, known_runs)
    if promoted:
        logger.info("已晋升 %d 个执行产物到 outputs/: %s", len(promoted), promoted)
    register_output_artifacts(state)
    return state


def _run_ids(workspace_dir: str) -> set[str]:
    runs = Path(workspace_dir) / "runs"
    if not runs.exists():
        return set()
    return {path.name for path in runs.iterdir() if path.is_dir()}


def promote_new_run_artifacts(workspace_dir: str, known_runs: set[str]) -> list[str]:
    """Copy artifacts produced by runs newer than `known_runs` into outputs/.

    CodeAgent writes every execution's files under runs/<run_id>/artifacts/. The
    legacy MainAgent promoted them by hand through a tool; the graph does it
    deterministically for whatever the gather step just produced.
    """
    workspace = Path(workspace_dir)
    runs_dir = workspace / "runs"
    if not runs_dir.exists():
        return []
    try:
        from services.file_services.workspace_fs import WorkspaceFS

        fs = WorkspaceFS(str(workspace))
    except Exception:
        fs = None
    promoted: list[str] = []
    for run_dir in sorted(path for path in runs_dir.iterdir() if path.is_dir() and path.name not in known_runs):
        artifacts_dir = run_dir / "artifacts"
        if not artifacts_dir.exists():
            continue
        for artifact in sorted(artifacts_dir.iterdir()):
            if not artifact.is_file() or artifact.suffix.lower() not in IMAGE_SUFFIXES | DATA_SUFFIXES:
                continue
            target_name = artifact.name
            if (workspace / "outputs" / target_name).exists():
                target_name = f"{run_dir.name[-6:]}_{artifact.name}"
            try:
                if fs is not None:
                    fs.promote_artifact(run_dir.name, artifact.name, target_name)
                else:
                    dest = workspace / "outputs" / target_name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(artifact.read_bytes())
                promoted.append(target_name)
            except Exception as exc:
                logger.warning("晋升产物失败 %s: %s", artifact, exc)
    return promoted


def register_output_artifacts(state: PaperState) -> None:
    if state.ir is None:
        return
    outputs = Path(state.workspace_dir) / "outputs"
    if not outputs.exists():
        return
    for path in sorted(outputs.iterdir()):
        if not path.is_file() or path.name.startswith("_") or path.suffix.lower() not in IMAGE_SUFFIXES | DATA_SUFFIXES:
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
        ".json": "application/json",
    }.get(suffix.lower(), "application/octet-stream")
