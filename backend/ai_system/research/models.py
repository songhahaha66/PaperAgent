"""Commands are proposals. Only ResearchStore may publish business state."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["revise", "add"] = "revise"
    task_id: str
    title: str = ""
    kind: Literal["compute", "writing"] = "compute"
    objective: str = Field(min_length=1)
    dependencies: list[str] | None = None


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=1)
    base_revision: int = Field(ge=1)
    reason: str = Field(min_length=1)
    changes: list[TaskChange] = Field(min_length=1)


class WorkOrder(BaseModel):
    attempt_id: str
    task_id: str
    revision: int
    input_digest: str
    lease: str
    input_refs: dict[str, str]


class WorkerResult(BaseModel):
    order: WorkOrder
    execution_status: Literal["succeeded", "failed", "cancelled"]
    acceptance: Literal["accepted", "rejected", "inconclusive"]
    report: dict[str, Any]
    design: dict[str, Any] | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class ResearchConflict(ValueError):
    """A command or attempt was based on state which is no longer current."""


class PlanProposal(BaseModel):
    reason: str
    changes: list[TaskChange] = Field(default_factory=list, max_length=6)
    evidence_refs: list[str] = Field(default_factory=list)
