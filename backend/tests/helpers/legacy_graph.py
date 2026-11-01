"""Schemas for importing historical research fixtures, not the live Agent."""
from typing import List, Literal
from pydantic import BaseModel, Field, model_validator
from ai_system.research.models import IssueReport


class Subproblem(BaseModel):
    id: str = Field(description="子问题编号，如 Q1、Q2")
    kind: Literal["compute", "verify"] = Field(
        default="compute",
        description="compute=获取研究证据的实验；verify=独立核查。论文与文书属于研究级交付，不建立写作实验",
    )
    title: str = Field(min_length=1, description="子问题的简短标题")
    question: str = Field(min_length=1, description="子问题要回答的具体内容，忠实于原题")
    deliverables: List[str] = Field(default_factory=list, description="这一问需要给出的结果，如某个预测值、排序、方案、图")
    depends_on: List[str] = Field(default_factory=list, description="依赖哪些子问题的结果（编号），没有依赖则为空")


class MethodPick(BaseModel):
    method_keys: List[str] = Field(description="从方法库中挑选 2~4 个最合适的方法 key")
    reason: str


class ModelCandidate(BaseModel):
    # 名称、思路、步骤不能为空：模型偶尔返回全空的占位方案，空字符串也能通过类型校验
    name: str = Field(min_length=1, description="方案名称")
    method_keys: List[str] = Field(description="使用的方法库 key，可组合多个")
    idea: str = Field(min_length=1, description="核心思路，2~4 句")
    formulation: str = Field(description="模型的数学表述要点：变量、目标/方程、约束，可用 LaTeX")
    steps: List[str] = Field(min_length=1, description="求解步骤，每步能用代码实现")
    assumptions: List[str] = Field(default_factory=list, description="方案采用的建模前提；与原题事实区分")
    validation: str = Field(description="如何检验结果和做灵敏度分析")

    @model_validator(mode="after")
    def _not_placeholder(self):
        # DeepSeek 还会把 schema 当模板原样填回 "placeholder"，2024_C 的 Q1 就按「placeholder」方案求解了
        if self.name.strip().lower() in {'placeholder', 'string', '...', '…', 'todo', 'tbd', '待定', '示例', '方案名称', '核心思路', 'name', 'idea'} or self.idea.strip().lower() in {'placeholder', 'string', '...', '…', 'todo', 'tbd', '待定', '示例', '方案名称', '核心思路', 'name', 'idea'}:
            raise ValueError("候选方案是占位内容，请写出具体的方案名称和思路")
        return self


class CandidateSet(BaseModel):
    # 空列表也能通过校验，模型偶尔这样敷衍；要求至少一个，校验失败会进入重试
    candidates: List[ModelCandidate] = Field(min_length=1)





