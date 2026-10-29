"""
基于 TypeSafe Jev（System One API）的判断器

连接参数可显式传入；未提供时使用 SDK 的 TYPESAFE_* 部署默认。
评测时应显式 pin 版本（如 jev-1.14.0），保证前后结果可比。
"""

import os
from typing import Any, Mapping, Optional

from .base import (
    Answers,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
)


def jev_available() -> bool:
    return bool(os.getenv("TYPESAFE_API_KEY"))


def _to_sdk(question: Question):
    from typesafe_sdk import Choice as SdkChoice, Noul as SdkNoul, Score as SdkScore

    if isinstance(question, Noul):
        return SdkNoul(instructions=question.instructions)
    if isinstance(question, Choice):
        return SdkChoice(instructions=question.instructions, criteria=dict(question.criteria))
    if isinstance(question, Score):
        return SdkScore(instructions=question.instructions, criteria=list(question.criteria))
    raise TypeError(f"不支持的问题类型: {type(question).__name__}")


class JevJudge:
    name = "jev"

    def __init__(self, model: Optional[str] = None, client: Any = None, *,
                 api_key: Optional[str] = None, base_url: Optional[str] = None,
                 timeout: Optional[float] = None):
        """
        Args:
            model: Jev 模型 ID，None 时用 SDK 默认值
            client: 已构造的 AsyncTypeSafeClient（测试时可注入替身）
        """
        self.model = model
        self._client = client
        self._owned_client = client is None
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout

    def _get_client(self):
        if self._client is None:
            from typesafe_sdk import AsyncTypeSafeClient

            self._client = AsyncTypeSafeClient(model=self.model, api_key=self._api_key,
                                              base_url=self._base_url, timeout=self._timeout)
        return self._client

    async def aclose(self):
        if self._owned_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ask(self, state: Any, questions: Mapping[str, Question]) -> Answers:
        client = self._get_client()
        response = await client.system_one(
            state, {key: _to_sdk(q) for key, q in questions.items()}
        )

        answers = Answers(judge=f"jev:{getattr(response, 'model', self.model or 'default')}")
        usage = getattr(response, "usage", None)
        answers.input_tokens = getattr(usage, "input_tokens", None)

        for key, a in response.nouls.items():
            answers.nouls[key] = NoulAnswer(noul=a.noul)
        for key, a in response.choices.items():
            answers.choices[key] = ChoiceAnswer(
                choice=a.choice, probabilities=dict(a.probabilities), confidence=a.confidence
            )
        for key, a in response.scores.items():
            answers.scores[key] = ScoreAnswer(
                score=a.score,
                probabilities={int(level): p for level, p in a.probabilities.items()},
                confidence=a.confidence,
            )
        return answers
