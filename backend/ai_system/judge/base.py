from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Question:
    instructions: str


@dataclass
class Noul(Question):
    kind: str = "noul"


@dataclass
class Choice(Question):
    criteria: dict[str, str] = field(default_factory=dict)
    kind: str = "choice"


@dataclass
class Score(Question):
    criteria: list[str] = field(default_factory=list)
    kind: str = "score"


@dataclass
class Answer:
    value: Any
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


class Judge(ABC):
    @abstractmethod
    def ask(self, state: dict, questions: dict[str, Question]) -> dict[str, Answer]:
        raise NotImplementedError
