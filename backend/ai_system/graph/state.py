"""
状态图的状态与结构化输出定义

状态里只放可序列化的 dict / list / str，方便 checkpoint 落盘和断点续跑；
LLM、内核、登记表这类运行时对象放在 GraphContext 里（不进 checkpoint）。
"""

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from pydantic import BaseModel, Field


def merge_dict(left: Optional[Dict], right: Optional[Dict]) -> Dict:
    """并行分支各自写入自己的 key，合并时后写覆盖先写"""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


# ----------------------------------------------------------------------
# 结构化输出
# ----------------------------------------------------------------------


class Subproblem(BaseModel):
    id: str = Field(description="子问题编号，如 Q1、Q2")
    title: str = Field(description="子问题的简短标题")
    question: str = Field(description="子问题要回答的具体内容，忠实于原题")
    deliverables: List[str] = Field(default_factory=list, description="这一问需要给出的结果，如某个预测值、排序、方案、图")
    depends_on: List[str] = Field(default_factory=list, description="依赖哪些子问题的结果（编号），没有依赖则为空")


class ProblemAnalysis(BaseModel):
    title: str = Field(description="论文标题，概括研究对象和主要方法")
    restatement: str = Field(description="用自己的话重述题目背景和要求")
    overall_analysis: str = Field(description="整体思路：各子问题之间的关系、数据如何使用")
    subproblems: List[Subproblem]
    assumptions: List[str] = Field(description="全局建模假设，每条说明理由")


class MethodPick(BaseModel):
    method_keys: List[str] = Field(description="从方法库中挑选 2~4 个最合适的方法 key")
    reason: str


class ModelCandidate(BaseModel):
    name: str = Field(description="方案名称")
    method_keys: List[str] = Field(description="使用的方法库 key，可组合多个")
    idea: str = Field(description="核心思路，2~4 句")
    formulation: str = Field(description="模型的数学表述要点：变量、目标/方程、约束，可用 LaTeX")
    steps: List[str] = Field(description="求解步骤，每步能用代码实现")
    assumptions: List[str] = Field(default_factory=list, description="本方案额外需要的假设")
    validation: str = Field(description="如何检验结果和做灵敏度分析")


class CandidateSet(BaseModel):
    candidates: List[ModelCandidate]


class SolveReport(BaseModel):
    summary: str = Field(description="求解过程和结论的概要，结论中的数值用 {{R:Rn}} 引用")
    approach: str = Field(description="实际采用的模型与算法，与计划不同时说明原因")
    key_findings: List[str] = Field(description="主要结论，每条引用登记的结果")
    sensitivity: str = Field(default="", description="灵敏度分析或检验的做法与结论")
    limitations: List[str] = Field(default_factory=list, description="局限与可改进之处")


class ResultReview(BaseModel):
    sound: bool = Field(description="结果是否可以直接用于写作")
    issues: List[str] = Field(default_factory=list, description="发现的问题，每条写清依据")
    must_fix: List[str] = Field(default_factory=list, description="必须回去重做或补充的事项，每条可直接执行；没有则为空")


# ----------------------------------------------------------------------
# 图状态
# ----------------------------------------------------------------------


class PaperState(TypedDict, total=False):
    task: str
    data_profile: str
    analysis: Dict[str, Any]
    # 子问题编号 -> {"methods", "candidates", "scores", "chosen", "critic"}
    designs: Annotated[Dict[str, Dict[str, Any]], merge_dict]
    feedback: str
    approved: bool
    design_round: int
    # 子问题编号 -> SolveReport dict + status
    solutions: Annotated[Dict[str, Dict[str, Any]], merge_dict]
    # 章节 key -> 带引用的 markdown 正文
    sections: Annotated[Dict[str, str], merge_dict]
    issues: List[Dict[str, str]]
    repair_round: int
    paper_path: str
    log: Annotated[List[str], operator.add]


class BranchInput(TypedDict, total=False):
    """Send 给单个子问题分支的输入"""

    subproblem: Dict[str, Any]
    analysis: Dict[str, Any]
    data_profile: str
    feedback: str
    design: Dict[str, Any]
    dependencies: Dict[str, Dict[str, Any]]
