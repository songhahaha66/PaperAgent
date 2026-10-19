"""
状态图的状态与结构化输出定义

状态里只放可序列化的 dict / list / str，方便 checkpoint 落盘和断点续跑；
LLM、内核、登记表这类运行时对象放在 GraphContext 里（不进 checkpoint）。
"""

import operator
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, model_validator
from ..research.models import Contract, Requirement, Assumption, AssumptionDraft, IssueReport


def merge_dict(left: Optional[Dict], right: Optional[Dict]) -> Dict:
    """并行分支各自写入自己的 key，合并时后写覆盖先写"""
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


# ----------------------------------------------------------------------
# 结构化输出
# ----------------------------------------------------------------------


class Subproblem(BaseModel):
    id: str = Field(description="子问题编号，如 Q1、Q2")
    kind: Literal["compute", "writing", "verify"] = Field(
        default="compute",
        description="compute=需要用数据建模计算；writing=只需基于前序结论写成文书（如给教练的备忘录、建议信、政策建议），不需要新的计算",
    )
    title: str = Field(min_length=1, description="子问题的简短标题")
    question: str = Field(min_length=1, description="子问题要回答的具体内容，忠实于原题")
    deliverables: List[str] = Field(default_factory=list, description="这一问需要给出的结果，如某个预测值、排序、方案、图")
    depends_on: List[str] = Field(default_factory=list, description="依赖哪些子问题的结果（编号），没有依赖则为空")


class ProblemAnalysis(BaseModel):
    title: str = Field(description="论文标题，概括研究对象和主要方法")
    restatement: str = Field(description="用自己的话重述题目背景和要求")
    overall_analysis: str = Field(description="整体思路：各子问题之间的关系、数据如何使用")
    # 空列表能通过类型校验，DeepSeek 偶尔这样返回；要求至少一个，校验失败会进入重试
    subproblems: List[Subproblem] = Field(min_length=1)
    requirements: List[Requirement] = Field(min_length=1, description="逐条提取原题目标、交付物、硬约束，写明原文摘录、承担任务与可执行验收条件")
    assumptions: List[Assumption] = Field(description="有编号、任务范围和可证伪检查的建模假设；不要把用户给定事实写成假设")


class MethodPick(BaseModel):
    method_keys: List[str] = Field(description="从方法库中挑选 2~4 个最合适的方法 key")
    reason: str


PLACEHOLDERS = {"placeholder", "string", "...", "…", "todo", "tbd", "待定", "示例", "方案名称", "核心思路", "name", "idea"}


class ModelCandidate(BaseModel):
    # 名称、思路、步骤不能为空：模型偶尔返回全空的占位方案，空字符串也能通过类型校验
    name: str = Field(min_length=1, description="方案名称")
    method_keys: List[str] = Field(description="使用的方法库 key，可组合多个")
    idea: str = Field(min_length=1, description="核心思路，2~4 句")
    formulation: str = Field(description="模型的数学表述要点：变量、目标/方程、约束，可用 LaTeX")
    steps: List[str] = Field(min_length=1, description="求解步骤，每步能用代码实现")
    assumptions: List[AssumptionDraft] = Field(default_factory=list, description="本方案实际依赖的新增关键前提；已有研究契约中的前提不要重复创建")
    validation: str = Field(description="如何检验结果和做灵敏度分析")

    @model_validator(mode="after")
    def _not_placeholder(self):
        # DeepSeek 还会把 schema 当模板原样填回 "placeholder"，2024_C 的 Q1 就按「placeholder」方案求解了
        if self.name.strip().lower() in PLACEHOLDERS or self.idea.strip().lower() in PLACEHOLDERS:
            raise ValueError("候选方案是占位内容，请写出具体的方案名称和思路")
        return self


class CandidateSet(BaseModel):
    # 空列表也能通过校验，模型偶尔这样敷衍；要求至少一个，校验失败会进入重试
    candidates: List[ModelCandidate] = Field(min_length=1)


STRENGTHS = ("显著", "倾向", "不显著")


class ResultItem(BaseModel):
    name: str = Field(description="结果名称")
    value: str = Field(description="单个数值、一小段文字，或同一变量的完整一维数值序列（JSON数组字符串）；必须来自run_id的输出。不同指标各登记一条，不能拼接整行输出。序列的长度、最小值等数值检查需另登记标量")
    run_id: str = Field(description="打印该值的那次执行的 run_id")
    unit: str = Field(default="", description="只填物理或计数单位（如 点、秒、米、万元、%），p 值、AUC、系数、比例这类无量纲量留空")
    description: str = Field(default="", description="可选补充说明；不用于指标身份、来源关联或有效性判定，无须为登记额外生成描述")


class Finding(BaseModel):
    statement: str = Field(description="一条结论，依据的数值写成 {{R:Rn}} 引用")
    strength: Literal["显著", "倾向", "不显著"] = Field(
        description="证据强度：显著须有对应统计检验证据；固定拆分上的实测改善或方向一致但证据有限=倾向；检验不显著或无法判断=不显著。数值差异不自动等于统计显著"
    )


class NegativeControl(BaseModel):
    metric: str = Field(description="预测效果指标名，如 测试集 AUC、LogLoss、RMSE")
    value: str = Field(description="真实标签下的指标值，必须是 run_id 那次执行 print 出的数")
    run_id: str = Field(description="打印 value 的那次执行的 run_id")
    shuffled_value: str = Field(description="把原始结果列在构造任何特征和标签之前随机打乱、其余流程完全不变时同一指标的值（只打乱训练集标签不算）")
    shuffled_run_id: str = Field(description="打印 shuffled_value 的那次执行的 run_id")
    chance: float = Field(allow_inf_nan=False, description="同一指标的无信息水平，必须是有限数值，如AUC为0.5或常数预测实际算出的MAE/RMSE；不能填文字说明，百分数使用0到1的比例")
    higher_is_better: bool = Field(description="指标是否越大越好")
    baseline_name: str = Field(default="", description="最简单的有信息基线，只用显而易见的结构，如“只用发球方先验胜率”“只用上一期值”")
    baseline_value: str = Field(default="", description="该基线在同一划分上的同一指标值，必须是 baseline_run_id 那次执行 print 出的数")
    baseline_run_id: str = Field(default="", description="打印 baseline_value 的那次执行的 run_id")


class RequirementEvidence(BaseModel):
    requirement_id: str
    evidence_refs: List[str] = Field(min_length=1, description="实际登记的原始编号数组，例如['R3','F2']；这里不能使用正文模板{{R:R3}}或{{F:F2}}")


class SolveReport(BaseModel):
    summary: str = Field(description="求解过程和结论的概要，结论中的数值用 {{R:Rn}} 引用")
    approach: str = Field(description="实际采用的模型与算法，与计划不同时说明原因")
    key_findings: List[Finding] = Field(description="主要结论，每条写明依据的登记结果和证据强度")
    sensitivity: str = Field(default="", description="灵敏度分析或检验的做法与结论")
    limitations: List[str] = Field(default_factory=list, description="局限与可改进之处")
    assumptions: List[AssumptionDraft] = Field(default_factory=list, description="仅提交本次新发现、改变方法或结论的关键前提；没有新增必须为[]。不是论文假设汇总：原题给定数据、已经计算验证的数据长度/完整性、契约中的已有前提均不填。不要换key、改写或合并已有前提来重复声明；真正的新前提登记冻结后须重算")
    requirement_evidence: List[RequirementEvidence] = Field(default_factory=list, description="每项本任务要求的证据引用；必须使用自己或前序任务的实际登记编号")
    research_issues: List[IssueReport] = Field(default_factory=list, description="发现上游模型、数据或假设可能有错时提交带证据的疑点；不要擅自沿用错误前提")
    controls: List[NegativeControl] = Field(
        default_factory=list, description="仅填实际执行且适用于本问题的标签置换对照；普通样本外预测不强制附加此实验，未做时留空，不编造"
    )


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
    problem: str = Field(description="本节哪句话与哪条结论（编号）不一致，应怎样改")
    quote: str = Field(default='',max_length=1200,description="可选：逐字复制本节问题原文；不能精确引用则留空，不编造引文")
    research_issue: IssueReport | None = Field(default=None, description="若根因是数据、模型或假设错误，提交研究问题而非只改措辞")


class ClaimAudit(BaseModel):
    issues: List[ClaimIssue] = Field(default_factory=list, description="没有不一致则为空")


class ResultReview(Contract):
    issues: List[IssueReport] = Field(default_factory=list, description="当前仍未解决的实质问题；逐项核对先前意见，全部有依据地解决后留空。填写目标任务、证据和适用检查，不把怀疑当事实")


# ----------------------------------------------------------------------
# 图状态
# ----------------------------------------------------------------------


class PaperState(TypedDict, total=False):
    artifact_conflict: bool
    research_restart: bool
    research_blocked: bool
    task: str
    data_profile: str
    analysis: Dict[str, Any]
    # 子问题编号 -> {"methods", "candidates", "scores", "chosen", "critic"}
    plan_ahead: bool  # True：全部子问题先设计再统一确认；False：每个子问题轮到求解时才设计
    designs: Annotated[Dict[str, Dict[str, Any]], merge_dict]
    confirmation_feedback: str
    approved: bool
    design_round: int
    # 子问题编号 -> SolveReport dict + status
    solutions: Annotated[Dict[str, Dict[str, Any]], merge_dict]
    # 用户模板的最上层章节 [{"key", "title"}]，没有模板时为空（用默认数模结构）
    section_contexts: Dict[str, str]
    outline: List[Dict[str, str]]
    # 章节 key -> 带引用的 markdown 正文
    sections: Annotated[Dict[str, str], merge_dict]
    # 跨子问题的结论冲突及统一口径
    consistency: List[Dict[str, Any]]
    issues: List[Dict[str, str]]
    repair_round: int
    paper_path: str
    log: Annotated[List[str], operator.add]


class BranchInput(TypedDict, total=False):
    """Send 给单个子问题分支的输入"""

    source: str
    research_contract: Dict[str, Any]
    review_findings: List[Dict[str, Any]]
    subproblem: Dict[str, Any]
    analysis: Dict[str, Any]
    data_profile: str
    confirmation_feedback: str
    design: Dict[str, Any]
    dependencies: Dict[str, Dict[str, Any]]
