from __future__ import annotations

import json
import re

from .base import Answer, Choice, Judge, Noul, Question, Score
from .heuristic import HeuristicJudge


class LLMJudge(Judge):
    def __init__(self, llm=None):
        self.llm = llm
        self._fallback = HeuristicJudge()

    def ask(self, state: dict, questions: dict[str, Question]) -> dict[str, Answer]:
        if self.llm is None or not questions:
            return self._fallback.ask(state, questions)
        try:
            from langchain_core.messages import HumanMessage

            prompt = (
                "根据 state 回答下列闭集问题，只输出 JSON 对象，键是问题 id，值是 {{value, confidence}}。\n"
                f"state={json.dumps(state, ensure_ascii=False)[:4000]}\n"
                f"questions={_describe(questions)}"
            )
            response = self.llm.invoke([HumanMessage(content=prompt)])
            text = getattr(response, "content", "") or ""
            payload = json.loads(_extract_json(text))
            answers = {}
            for key, question in questions.items():
                item = payload.get(key) or {}
                answers[key] = Answer(
                    value=item.get("value", False if isinstance(question, Noul) else "other"),
                    confidence=float(item.get("confidence") or 0.5),
                )
            return answers
        except Exception:
            return self._fallback.ask(state, questions)


def _describe(questions: dict[str, Question]) -> str:
    rows = []
    for key, question in questions.items():
        extra = ""
        if isinstance(question, Choice):
            extra = f" choices={list(question.criteria)}"
        elif isinstance(question, Score):
            extra = f" scores={question.criteria}"
        rows.append(f"{key}: {question.instructions}{extra}")
    return "\n".join(rows)


def _extract_json(text: str) -> str:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    return text[start : end + 1] if start >= 0 and end > start else text
