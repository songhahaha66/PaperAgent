from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

from .base import Answer, Choice, Judge, Noul, Question, Score
from .heuristic import HeuristicJudge

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"


class JevJudge(Judge):
    """TypeSafe System One / Jev. Falls back on missing key, timeout, or bad payload."""

    def __init__(
        self,
        api_key: str | None = None,
        fallback: Judge | None = None,
        request_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        timeout: float = 8,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY")
        self._fallback = fallback or HeuristicJudge()
        self._request_fn = request_fn
        self.timeout = timeout
        self.base_url = base_url or os.getenv("TYPESAFE_API_BASE") or DEFAULT_URL
        self.model = model or os.getenv("TYPESAFE_MODEL") or "jev-latest"

    def ask(self, state: dict, questions: dict[str, Question]) -> dict[str, Answer]:
        if not questions:
            return {}
        if not self.api_key and self._request_fn is None:
            return self._fallback.ask(state, questions)
        try:
            payload = {
                "model": self.model,
                "state": state,
                "questions": {key: serialize_question(question) for key, question in questions.items()},
            }
            body = self._request_fn(payload) if self._request_fn else self._http_post(payload)
            parsed = parse_jev_answers(body, questions)
            if parsed:
                return parsed
        except Exception:
            pass
        return self._fallback.ask(state, questions)

    def _http_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Jev HTTP {exc.code}") from exc


def serialize_question(question: Question) -> dict[str, Any]:
    if isinstance(question, Choice):
        return {"type": "choice", "instructions": question.instructions, "criteria": question.criteria}
    if isinstance(question, Score):
        return {"type": "score", "instructions": question.instructions, "criteria": question.criteria}
    if isinstance(question, Noul):
        return {
            "type": "noul",
            "instructions": question.instructions,
            "criteria": {"true": "是", "false": "否"},
        }
    return {"type": "noul", "instructions": getattr(question, "instructions", "")}


def parse_jev_answers(body: dict[str, Any], questions: dict[str, Question]) -> dict[str, Answer]:
    raw = body.get("answers") if isinstance(body.get("answers"), dict) else body
    if not isinstance(raw, dict):
        return {}
    answers: dict[str, Answer] = {}
    for key, question in questions.items():
        item = raw.get(key)
        if not isinstance(item, dict):
            continue
        if isinstance(question, Noul) or "noul" in item:
            noul = float(item.get("noul", item.get("value", 0)) or 0)
            answers[key] = Answer(
                value=noul >= 0.5,
                confidence=max(noul, 1 - noul),
                probabilities={"true": noul, "false": 1 - noul},
            )
        elif isinstance(question, Score) or "score" in item:
            answers[key] = Answer(
                value=item.get("score", item.get("value")),
                confidence=float(item.get("confidence") or 0.5),
                probabilities=item.get("probabilities") or {},
            )
        else:
            choice = item.get("choice", item.get("value"))
            answers[key] = Answer(
                value=choice,
                confidence=float(item.get("confidence") or 0.5),
                probabilities=item.get("probabilities") or {},
            )
    return answers
