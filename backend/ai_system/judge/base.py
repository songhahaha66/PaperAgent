"""
判断层的统一接口

问题类型与 TypeSafe System One 的三种原语一一对应：
- Noul：是/否，返回"是"的概率
- Choice：从闭集中选一个，返回各选项概率与置信度
- Score：按有序等级打分，返回期望等级（0 起）、各等级概率与置信度

所有实现（Jev、LLM 回退）都返回同一种 Answers，调用方不关心底层是谁。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Union


@dataclass(frozen=True)
class Noul:
    instructions: str


@dataclass(frozen=True)
class Choice:
    instructions: str
    # 选项名 -> 选项描述（None 表示名字本身已足够清楚）
    criteria: Dict[str, Optional[str]]


@dataclass(frozen=True)
class Score:
    instructions: str
    # 有序等级，从低到高
    criteria: List[str]


Question = Union[Noul, Choice, Score]


@dataclass
class NoulAnswer:
    noul: float


@dataclass
class ChoiceAnswer:
    choice: str
    probabilities: Dict[str, float]
    # LLM 回退无校准置信度，此时为 None
    confidence: Optional[float]


@dataclass
class ScoreAnswer:
    # 期望等级：sum(i * p_i)，范围 [0, len(levels) - 1]
    score: float
    probabilities: Dict[int, float]
    confidence: Optional[float]
    # LLM 回退会给出理由；Jev 不生成文本
    reason: str = ""


@dataclass
class Answers:
    nouls: Dict[str, NoulAnswer] = field(default_factory=dict)
    choices: Dict[str, ChoiceAnswer] = field(default_factory=dict)
    scores: Dict[str, ScoreAnswer] = field(default_factory=dict)
    # 判断器名称，写入评测结果便于区分
    judge: str = ""
    input_tokens: Optional[int] = None


class Judge(Protocol):
    name: str

    async def ask(self, state: Any, questions: Mapping[str, Question]) -> Answers:
        """对同一份 state 一次性回答多个问题"""
        ...
