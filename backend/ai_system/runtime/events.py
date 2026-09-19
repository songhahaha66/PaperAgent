from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..schemas.events import JSON_BLOCK_EVENT_MAP, RunEventPayload


def persist_run_event(event: RunEventPayload, db=None) -> None:
    """Best-effort write to run_events. Missing DB/session must not break streaming."""
    if db is None:
        return
    try:
        from models.models import RunEvent

        db.add(
            RunEvent(
                run_id=event.run_id,
                thread_id=event.thread_id,
                offset=event.offset,
                event_type=event.event_type,
                payload=event.payload,
            )
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


class EventEmitter:
    """Persist AG-UI style events and dual-write alongside json_block."""

    def __init__(
        self,
        workspace_dir: str | Path | None = None,
        db=None,
        run_id: str = "",
        thread_id: str = "",
    ):
        self.workspace_dir = Path(workspace_dir) if workspace_dir else None
        self.db = db
        self.run_id = run_id
        self.thread_id = thread_id
        self._offset = 0
        self.events: list[RunEventPayload] = []

    @property
    def path(self) -> Path | None:
        if not self.workspace_dir:
            return None
        return self.workspace_dir / ".system" / "run_events.jsonl"

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        run_id: str = "",
        thread_id: str = "",
    ) -> RunEventPayload:
        self._offset += 1
        event = RunEventPayload(
            event_type=event_type,
            run_id=run_id or self.run_id,
            thread_id=thread_id or self.thread_id,
            offset=self._offset,
            payload=payload or {},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.events.append(event)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
        persist_run_event(event, self.db)
        return event

    def emit_from_json_block(self, block_type: str, content: Any, run_id: str = "", thread_id: str = "") -> RunEventPayload:
        event_type = JSON_BLOCK_EVENT_MAP.get(block_type, "CUSTOM")
        payload: dict[str, Any] = {"type": block_type}
        if isinstance(content, (dict, list)):
            payload["content"] = content
        else:
            payload["content"] = str(content)
        return self.emit(event_type, payload, run_id=run_id, thread_id=thread_id)

    def load(self) -> list[RunEventPayload]:
        if not self.path or not self.path.exists():
            return list(self.events)
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(RunEventPayload.model_validate(json.loads(line)))
        return rows
