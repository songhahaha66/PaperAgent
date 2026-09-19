from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EditIntent(BaseModel):
    kind: Literal["write", "edit", "question", "chat", "confirm"] = "write"
    target_slots: list[str] = Field(default_factory=list)
    reason: str = ""
