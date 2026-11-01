"""
状态图的状态与结构化输出定义

GraphState 只保存流程控制字段；PaperState 是从 ResearchStore 派生的临时写作视图；
LLM、内核、登记表这类运行时对象放在 GraphContext 里（不进 checkpoint）。
"""

from typing import Any, Dict, List, Literal, TypedDict

from pydantic import BaseModel, Field
from ..research.models import Contract, IssueReport


# ----------------------------------------------------------------------
# 结构化输出
# ----------------------------------------------------------------------


class Conflict(BaseModel):
    claims: List[str] = Field(description="涉及的结论编号，如 [\"C2\", \"C7\"]；结论与自身证据不符时只填一个")
    issue: str = Field(description="哪里矛盾，引用结论原文说明")
    resolution: str = Field(description="写作时统一采用的口径：怎样表述才与全部证据一致")
    research_issue: IssueReport | None = Field(default=None, description="涉及数据、模型或假设错误时提交研究问题，进入独立核验与重规划")


class ConsistencyReview(BaseModel):
    conflicts: List[Conflict] = Field(default_factory=list, description="没有矛盾则为空")


class ClaimIssue(BaseModel):
    contradicts: bool = Field(default=True, description="这句话是否确实与结论表矛盾；一致、或只是没有提到的，填 false")
    section: str = Field(description="有问题的章节 key")
    targets: List[str] = Field(default_factory=list, description='选择系统提供的源码片段标记，可选多个；不能定位时留空，作为文件级意见，不编造标记')
    problem: str = Field(description="本节哪句话与哪条结论（编号）不一致，应怎样改")
    quote: str = Field(default='',max_length=1200,description="可选摘录，仅供阅读；LaTeX 定位使用 targets，无需复制原文")
    research_issue: IssueReport | None = Field(default=None, description="若根因是数据、模型或假设错误，提交研究问题而非只改措辞")


class SectionIssueReview(BaseModel):
    issue_id: str
    verdict: Literal['writing', 'research', 'dismissed']
    reason: str = Field(min_length=1, description='结合当前原文及登记证据，说明归类或撤销的理由')
    finding: ClaimIssue | None = Field(default=None, description='旧意见仍需处理时在此提供具体问题；撤销时为空。不在新意见 issues 中重复登记')


class ClaimAudit(BaseModel):
    issues: List[ClaimIssue] = Field(default_factory=list, description="没有不一致则为空")
    issue_reviews: List[SectionIssueReview] = Field(default_factory=list, description='逐项重新核对提供的旧章节疑点；不能凭未提及旧意见就关闭它')


class ResultReview(Contract):
    issues: List[IssueReport] = Field(default_factory=list, description="当前仍未解决的实质问题；逐项核对先前意见，全部有依据地解决后留空。填写目标任务、证据和适用检查，不把怀疑当事实")


# ----------------------------------------------------------------------
# 临时写作视图（从 ResearchStore 派生，不进入 checkpoint）
# ----------------------------------------------------------------------


class PaperState(TypedDict, total=False):
    artifact_conflict: bool
    research_restart: bool
    research_blocked: bool
    task: str
    data_profile: str
    analysis: Dict[str, Any]
    # 子问题编号 -> {"methods", "candidates", "scores", "chosen", "critic"}
    designs: Dict[str, Dict[str, Any]]
    # 子问题编号 -> SolveReport dict + status
    solutions: Dict[str, Dict[str, Any]]
    # 收敛决策的结果大纲（含范围/依据）；用户模板结构优先。
    section_contexts: Dict[str, str]
    outline: List[Dict[str, Any]]
    # 章节 key -> 带引用的 markdown 正文
    sections: Dict[str, str]
    # 跨子问题的结论冲突及统一口径
    consistency: List[Dict[str, Any]]
    issues: List[Dict[str, str]]
    repair_round: int
    paper_path: str
    log: List[str]


class BranchInput(TypedDict, total=False):
    """Frozen context for one experiment, independent of the graph writing view."""

    source: str
    task_revision: int
    clarifications: List[Dict[str, Any]]
    research_contract: Dict[str, Any]
    review_findings: List[Dict[str, Any]]
    subproblem: Dict[str, Any]
    data_profile: str
    design: Dict[str, Any]
    dependencies: Dict[str, Dict[str, Any]]


class GraphState(TypedDict, total=False):
    """Checkpoint control only; business projections come from ResearchStore."""
    research_blocked: bool
