from __future__ import annotations

import os

from .heuristic import HeuristicJudge
from .jev import JevJudge
from .llm import LLMJudge


def get_judge(llm=None):
    heuristic = HeuristicJudge()
    llm_judge = LLMJudge(llm=llm) if llm is not None else heuristic
    api_key = os.getenv("TYPESAFE_API_KEY")
    if api_key:
        return JevJudge(api_key=api_key, fallback=llm_judge)
    return llm_judge
