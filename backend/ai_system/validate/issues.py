from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ValidationIssue(BaseModel):
    code: str
    slot_id: str | None = None
    severity: Literal["error", "warning"] = "error"
    detail: str = ""
