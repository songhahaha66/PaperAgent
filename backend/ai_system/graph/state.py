from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..schemas.intent import EditIntent
from ..schemas.paper_ir import PaperIR
from ..schemas.plan import Plan
from ..schemas.template_spec import TemplateSpec
from ..validate.issues import ValidationIssue


class PaperState(BaseModel):
    work_id: str
    run_id: str = ""
    workspace_dir: str = ""
    template_id: int | None = None
    output_mode: str = "markdown"
    user_message: str = ""
    intent: EditIntent = Field(default_factory=EditIntent)
    spec: TemplateSpec | None = None
    ir: PaperIR | None = None
    plan: Plan | None = None
    current_batch: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    repair_round: int = 0
    max_repair_rounds: int = 2
    slot_repairs: dict[str, int] = Field(default_factory=dict)
    messages: list[str] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)
    awaiting_confirmation: bool = False
    finished: bool = False
    summary: str = ""
    rendered_path: str = ""

    def model_dump_checkpoint(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
