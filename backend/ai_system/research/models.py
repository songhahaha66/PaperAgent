"""Research contracts shared by analysis, execution, checking and planning."""
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResultSelector(Contract):
    task_id: str
    name: str = Field(min_length=1, description="必须登记的单个结果名称，求解和验证使用相同名称。数值比较只接受标量；序列长度/非负性应分别命名长度/最小值等标量指标，不能引用整组数组")


class LinearVariable(Contract):
    name: str = Field(min_length=1, description="该决策变量必须单独登记的结果名称，与left属于同一任务")
    source_quote: str = Field(min_length=1, description="原题对变量定义、上下界或整数性的逐字摘录")
    lower: float | None = None
    upper: float | None = None
    integer: bool = False


class LinearConstraint(Contract):
    coefficients: list[float]
    relation: Literal["le", "ge", "eq"]
    rhs: float
    source_quote: str = Field(min_length=1, description="原题这条线性约束的逐字摘录")


class LinearModel(Contract):
    """Frozen explicit linear problem; feasibility is not an optimality certificate."""
    variables: list[LinearVariable] = Field(min_length=1)
    objective: list[float]
    objective_quote: str = Field(min_length=1, description="原题线性目标函数的逐字摘录")
    offset: float = 0
    constraints: list[LinearConstraint] = Field(default_factory=list)

    @model_validator(mode="after")
    def dimensions(self):
        size = len(self.variables)
        if len({v.name for v in self.variables}) != size:
            raise ValueError("线性模型变量登记名称不能重复")
        if len(self.objective) != size or any(len(c.coefficients) != size for c in self.constraints):
            raise ValueError("目标和约束系数必须与变量顺序和数量一致")
        numbers = [self.offset, *self.objective, *(x for c in self.constraints for x in [c.rhs, *c.coefficients])]
        numbers += [x for v in self.variables for x in (v.lower, v.upper) if x is not None]
        if not all(isfinite(x) for x in numbers):
            raise ValueError("线性模型系数和边界必须是有限数")
        if any(v.lower is not None and v.upper is not None and v.lower > v.upper for v in self.variables):
            raise ValueError("变量下界不能大于上界")
        return self


class DomainCheck(Contract):
    id: str
    description: str = Field(min_length=1)
    left: ResultSelector = Field(description="检查的登记指标；linear_feasible时必须选择独立登记的目标值，决策变量在linear_model.variables中列出")
    operator: Literal["exists", "finite", "le", "lt", "ge", "gt", "eq", "approx", "linear_feasible"]
    expected: float | None = Field(default=None, description="数值阈值；百分数使用0到1的比例，单位必须与登记结果一致")
    right: ResultSelector | None = None
    tolerance: float = Field(default=0, ge=0)
    linear_model: LinearModel | None = Field(default=None, exclude_if=lambda value: value is None,
        description="仅linear_feasible使用：原题明确给出的线性约束与目标；left选择报告的目标值。用于复核可行性和目标值，不证明最优")

    @model_validator(mode="after")
    def operands(self):
        unary = {"exists", "finite", "linear_feasible"}
        if self.operator not in unary and (self.expected is None) == (self.right is None):
            raise ValueError("比较检查必须且只能指定一个右操作数")
        if self.operator in unary and (self.expected is not None or self.right is not None):
            raise ValueError("存在性、有限性及线性可行性检查没有右操作数")
        if not isfinite(self.tolerance) or (self.expected is not None and not isfinite(self.expected)):
            raise ValueError("验收阈值必须是有限数")
        if (self.operator == "linear_feasible") != (self.linear_model is not None):
            raise ValueError("linear_feasible必须且只能使用线性模型")
        if self.linear_model and self.left.name in {v.name for v in self.linear_model.variables}:
            raise ValueError("目标值与决策变量必须分别登记")
        return self

    def selectors(self):
        return [self.left, *([self.right] if self.right else []),
                *([ResultSelector(task_id=self.left.task_id, name=v.name) for v in self.linear_model.variables]
                  if self.linear_model else [])]


class Requirement(Contract):
    id: str
    statement: str = Field(min_length=1)
    source_quote: str = Field(min_length=1, description="原题逐字摘录，必须能在用户需求中找到")
    kind: Literal["goal", "constraint", "deliverable"] = Field(description="goal=研究目标；constraint=必须满足且有可执行检查的数学/领域约束；deliverable=报告、语言、格式、引用等交付要求。不要为文书要求伪造满足指标")
    task_ids: list[str] = Field(min_length=1)
    checks: list[DomainCheck] = Field(default_factory=list, description="constraint至少一项可执行检查；goal或deliverable按实际需要可留空，文书要求由章节校验处理")

    @model_validator(mode="after")
    def constraint_check(self):
        if self.kind == "constraint" and not self.checks:
            raise ValueError(f"硬约束「{self.statement}」必须有可执行检查。数学/领域约束保持constraint并补检查；"
                             "报告格式、语言、引用规则属于deliverable，研究目标属于goal，不要为文书要求编造布尔指标")
        return self


class Assumption(Contract):
    id: str
    statement: str = Field(min_length=1)
    task_ids: list[str] = Field(min_length=1, description="依赖这个假设的任务范围")
    checks: list[DomainCheck] = Field(default_factory=list, description="可证伪条件，无法操作化时留空并保留为待验证假设")


class AssumptionDraft(Contract):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$", description="本任务内稳定的假设键；同一前提复用同一键")
    statement: str = Field(min_length=1, description="实际计算或结论依赖的关键前提，不记录一般说明或用户给定事实")
    checks: list[DomainCheck] = Field(default_factory=list, description="check.id使用本假设内稳定的局部键，系统分配全局编号；重复声明时复用原始局部键")


class AssumptionProposal(AssumptionDraft):
    source: Literal["design", "solve"]


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
    resolution_attempt: str | None = None
    source: str
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


class ReviewRecord(Contract):
    report_hash: str
    issues: list[IssueReport]
    evidence: list[dict[str, Any]] = Field(default_factory=list, description="审查实际引用的本次尝试证据，保留返工前已被替换的值")


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
    experience_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    review_required: bool = False
    review_issue_ids: list[str] = Field(default_factory=list)


class WorkerResult(Contract):
    order: WorkOrder
    execution_status: Literal["succeeded", "failed", "cancelled"]
    acceptance: Literal["accepted", "rejected", "inconclusive"]
    report: dict[str, Any]
    design: dict[str, Any] | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[IssueReport] = Field(default_factory=list)
    reviews: list[ReviewRecord] = Field(default_factory=list)
    assumptions: list[AssumptionProposal] = Field(default_factory=list)


class ResearchConflict(ValueError):
    """A command or attempt was based on state which is no longer current."""


class PlanProposal(Contract):
    reason: str
    changes: list[TaskChange] = Field(default_factory=list, max_length=6)
    assumptions: list[AssumptionChange] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
