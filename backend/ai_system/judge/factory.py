from __future__ import annotations

import os

from .heuristic import HeuristicJudge
from .jev import JevJudge
from .llm import LLMJudge


def get_judge(llm=None):
    api_key = os.getenv("TYPESAFE_API_KEY")
    if api_key:
        return JevJudge(api_key=api_key)
    if llm is not None:
        return LLMJudge(llm=llm)
    return HeuristicJudge()
