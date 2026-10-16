"""Research contracts shared by analysis, execution, checking and planning."""
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResultSelector(Contract):
    task_id: str
    name: str = Field(min_length=1, description="必须登记的单个结果名称，求解和验证使用相同名称")


class DomainCheck(Contract):
    id: str
    description: str = Field(min_length=1)
    left: ResultSelector
    operator: Literal["exists", "finite", "le", "lt", "ge", "gt", "eq", "approx"]
    expected: float | None = Field(default=None, description="数值阈值；百分数使用0到1的比例，单位必须与登记结果一致")
    right: ResultSelector | None = None
    tolerance: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def operands(self):
        if self.operator not in {"exists", "finite"} and (self.expected is None) == (self.right is None):
            raise ValueError("比较检查必须且只能指定一个右操作数")
        if self.operator in {"exists", "finite"} and (self.expected is not None or self.right is not None):
            raise ValueError("存在性/有限性检查没有右操作数")
        if not isfinite(self.tolerance) or (self.expected is not None and not isfinite(self.expected)):
            raise ValueError("验收阈值必须是有限数")
        return self


class Requirement(Contract):
    id: str
    statement: str = Field(min_length=1)
    source_quote: str = Field(min_length=1, description="原题逐字摘录，必须能在用户需求中找到")
    kind: Literal["goal", "constraint", "deliverable"]
    task_ids: list[str] = Field(min_length=1)
    checks: list[DomainCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def constraint_check(self):
        if self.kind == "constraint" and not self.checks:
            raise ValueError("硬约束必须有可执行检查；非数值约束可登记布尔满足指标并检查等于1")
        return self


class Assumption(Contract):
    id: str
    statement: str = Field(min_length=1)
    task_ids: list[str] = Field(min_length=1, description="依赖这个假设的任务范围")
    checks: list[DomainCheck] = Field(default_factory=list, description="可证伪条件，无法操作化时留空并保留为待验证假设")


class IssueReport(Contract):
    task_id: str
    category: Literal["domain", "assumption", "model", "data", "evidence", "execution", "writing", "coverage", "service"]
    message: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    assumption_id: str | None = None
    check_ids: list[str] = Field(default_factory=list)
    section: str | None = None


class Issue(IssueReport):
    id: str
    task_revision: int
    assumption_revision: int | None = None
    producer_attempt: str | None = None
    status: Literal["suspected", "confirmed", "repairing", "resolved", "dismissed", "inconclusive"]
    action: Literal["verify", "recompute", "retry", "rewrite"]
    blocking: bool = True
    repair_targets: dict[str, int] = Field(default_factory=dict)


class CheckResult(Contract):
    check_id: str
    outcome: Literal["passed", "failed", "inconclusive"]
    evidence_refs: list[str]
    message: str
    owner_kind: Literal["requirement", "assumption"]
    owner_id: str
    owner_revision: int


class ValidationRecord(Contract):
    protocol_hash: str
    checks: list[CheckResult]
    outcome: Literal["accepted", "rejected", "inconclusive"]


class AssumptionChange(Contract):
    assumption_id: str
    base_revision: int = Field(ge=1)
    statement: str = Field(min_length=1)
    task_ids: list[str] = Field(min_length=1)
    checks: list[DomainCheck] = Field(default_factory=list)
    status: Literal["active", "retired"] = "active"


class TaskChange(Contract):
    action: Literal["revise", "add"] = "revise"
    task_id: str
    title: str = ""
    kind: Literal["compute", "writing", "verify"] = "compute"
    objective: str = Field(min_length=1)
    dependencies: list[str] | None = None
    requirement_ids: list[str] | None = None


class PlanPatch(Contract):
    command_id: str = Field(min_length=1)
    base_revision: int = Field(ge=1)
    reason: str = Field(min_length=1)
    changes: list[TaskChange] = Field(default_factory=list)
    assumptions: list[AssumptionChange] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    amendment: str | None = None

    @model_validator(mode="after")
    def changes_something(self):
        if not self.changes and not self.assumptions:
            raise ValueError("计划修改不能为空")
        return self


class WorkOrder(Contract):
    attempt_id: str
    task_id: str
    revision: int
    input_digest: str
    lease: str
    input_refs: dict[str, str]
    protocol_hash: str


class WorkerResult(Contract):
    order: WorkOrder
    execution_status: Literal["succeeded", "failed", "cancelled"]
    acceptance: Literal["accepted", "rejected", "inconclusive"]
    report: dict[str, Any]
    design: dict[str, Any] | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[IssueReport] = Field(default_factory=list)


class ResearchConflict(ValueError):
    """A command or attempt was based on state which is no longer current."""


class PlanProposal(Contract):
    reason: str
    changes: list[TaskChange] = Field(default_factory=list, max_length=6)
    assumptions: list[AssumptionChange] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
