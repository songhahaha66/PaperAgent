from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "RUN_STARTED",
    "RUN_FINISHED",
    "RUN_ERROR",
    "STEP_STARTED",
    "STEP_FINISHED",
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "STATE_DELTA",
    "CUSTOM",
]


class RunEventPayload(BaseModel):
    event_type: str
    run_id: str = ""
    thread_id: str = ""
    offset: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


JSON_BLOCK_EVENT_MAP = {
    "main_agent_start": "RUN_STARTED",
    "main_agent_complete": "RUN_FINISHED",
    "main_agent_error": "RUN_ERROR",
    "continuation": "STEP_STARTED",
    "plan_updated": "STATE_DELTA",
}
