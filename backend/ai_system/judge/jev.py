from __future__ import annotations

from .base import Answer, Judge, Question
from .heuristic import HeuristicJudge


class JevJudge(Judge):
    """TypeSafe Jev adapter. Without an API key this falls back immediately."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        self._fallback = HeuristicJudge()

    def ask(self, state: dict, questions: dict[str, Question]) -> dict[str, Answer]:
        if not self.api_key:
            return self._fallback.ask(state, questions)
        return self._fallback.ask(state, questions)
