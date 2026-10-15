"""
LLM 回退判断器：没有 Jev key 时用任意 LangChain 聊天模型回答同一组问题

每个问题一次结构化输出调用，并行执行。答案是单点（概率 one-hot），
没有校准置信度，因此 confidence 为 None。
"""

import asyncio
import json
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, Field, create_model

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


def _render_state(state: Any) -> str:
    if isinstance(state, str):
        return state
    return json.dumps(state, ensure_ascii=False, indent=2)


def _schema_for(question: Question) -> type[BaseModel]:
    if isinstance(question, Noul):
        return create_model(
            "NoulVerdict",
            reason=(str, Field(description="简短理由")),
            answer=(bool, Field(description="问题的答案是否为“是”")),
        )
    if isinstance(question, Choice):
        options = tuple(question.criteria.keys())
        return create_model(
            "ChoiceVerdict",
            reason=(str, Field(description="简短理由")),
            choice=(Literal[options], Field(description="所选选项名")),
        )
    if isinstance(question, Score):
        levels = tuple(range(len(question.criteria)))
        return create_model(
            "ScoreVerdict",
            reason=(str, Field(description="简短理由")),
            level=(Literal[levels], Field(description="所选等级编号，从 0 开始")),
        )
    raise TypeError(f"不支持的问题类型: {type(question).__name__}")


def _render_question(question: Question) -> str:
    lines = [question.instructions]
    if isinstance(question, Choice):
        lines.append("选项：")
        for name, desc in question.criteria.items():
            lines.append(f"- {name}" + (f"：{desc}" if desc else ""))
    elif isinstance(question, Score):
        lines.append("等级（从低到高，编号从 0 开始）：")
        for i, level in enumerate(question.criteria):
            lines.append(f"{i}. {level}")
    return "\n".join(lines)


MAX_ATTEMPTS = 3


class LLMJudge:
    name = "llm"

    def __init__(self, llm: Any, label: Optional[str] = None):
        self.llm = llm
        model_name = getattr(llm, "model_name", None) or getattr(llm, "model", None)
        self.label = label or f"llm:{model_name or type(llm).__name__}"

    async def _ask_one(self, state_text: str, question: Question) -> BaseModel:
        schema = _schema_for(question)
        try:
            # OpenAI 兼容网关普遍支持工具调用，但不一定支持 json_schema
            structured = self.llm.with_structured_output(schema, method="function_calling")
        except (TypeError, ValueError, NotImplementedError):
            structured = self.llm.with_structured_output(schema)
        prompt = (
            "你是一名严格、客观的评审。只依据下面给出的材料回答，不要臆测材料之外的内容。\n\n"
            f"## 材料\n{state_text}\n\n## 问题\n{_render_question(question)}"
        )
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                verdict = await structured.ainvoke(prompt)
                if verdict is not None:
                    return verdict
                # 模型偶尔用文字作答而不调用工具，结构化结果为 None
                last_error = ValueError("模型没有返回结构化结果")
            except Exception as exc:
                last_error = exc
            await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"判分失败（重试 {MAX_ATTEMPTS} 次）: {last_error}") from last_error

    async def ask(self, state: Any, questions: Mapping[str, Question]) -> Answers:
        state_text = _render_state(state)
        keys = list(questions.keys())
        verdicts = await asyncio.gather(*(self._ask_one(state_text, questions[k]) for k in keys))

        answers = Answers(judge=self.label)
        for key, verdict in zip(keys, verdicts):
            question = questions[key]
            if isinstance(question, Noul):
                answers.nouls[key] = NoulAnswer(noul=1.0 if verdict.answer else 0.0)
            elif isinstance(question, Choice):
                answers.choices[key] = ChoiceAnswer(
                    choice=verdict.choice,
                    probabilities={name: float(name == verdict.choice) for name in question.criteria},
                    confidence=None,
                )
            else:
                answers.scores[key] = ScoreAnswer(
                    score=float(verdict.level),
                    probabilities={i: float(i == verdict.level) for i in range(len(question.criteria))},
                    confidence=None,
                    reason=verdict.reason,
                )
        return answers
