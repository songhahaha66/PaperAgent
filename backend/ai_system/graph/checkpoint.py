from __future__ import annotations

import json
from pathlib import Path

from .state import PaperState

CHECKPOINT_NAME = "checkpoint.json"


def checkpoint_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir) / ".system" / CHECKPOINT_NAME


def save_checkpoint(state: PaperState) -> Path:
    path = checkpoint_path(state.workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.model_dump_checkpoint(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_checkpoint(workspace_dir: str | Path) -> PaperState | None:
    path = checkpoint_path(workspace_dir)
    if not path.exists():
        return None
    try:
        return PaperState.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None
