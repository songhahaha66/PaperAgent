from __future__ import annotations

import re

from .base import Answer, Choice, Judge, Noul, Question, Score

PLACEHOLDER_RE = re.compile(r"(待填写|请在此|请填写|在此处|下划线|_{3,}|××+|占位|TODO)")
INSTRUCTION_RE = re.compile(r"(成稿后删除|格式要求|写作说明|请将此段删除|以下内容可根据|包含括号内容)")
EXAMPLE_RE = re.compile(r"(示例|样例|例如：|例如:|贪吃蛇|推箱子)")
CAPTION_RE = re.compile(r"^(图|表|figure|table)\s*[\d一二三四五六七八九十]+", re.I)
FIXED_RE = re.compile(r"(学号|姓名|指导教师|学院|专业|封面|签字|日期)")
WRITE_RE = re.compile(r"(写|生成|完成|起草|补充|继续)")
EDIT_RE = re.compile(r"(修改|改一下|重写|更新|调整)")
QUESTION_RE = re.compile(r"(什么|为什么|怎么|如何|吗|？|\?)")
CONFIRM_RE = re.compile(r"(确认采用|确认当前|按现状|停止修复|跳过校验|采用当前稿)")


def infer_role(kind: str, text: str, is_heading: bool = False) -> str:
    if kind == "table":
        return "table"
    if kind == "figure":
        return "figure_slot"
    text = (text or "").strip()
    if is_heading:
        return "heading"
    if CAPTION_RE.search(text):
        return "caption"
    if INSTRUCTION_RE.search(text):
        return "instruction_delete"
    if EXAMPLE_RE.search(text):
        return "example_delete"
    if PLACEHOLDER_RE.search(text):
        return "placeholder_fill"
    if FIXED_RE.search(text) and len(text) <= 40:
        return "fixed_text"
    return "other"


def infer_intent_kind(message: str) -> str:
    message = (message or "").strip()
    if CONFIRM_RE.search(message):
        return "confirm"
    if EDIT_RE.search(message):
        return "edit"
    if QUESTION_RE.search(message) and (
        message.endswith("？") or message.endswith("?") or (len(message) < 40 and not WRITE_RE.search(message))
    ):
        return "question" if len(message) < 80 else "chat"
    return "write"


class HeuristicJudge(Judge):
    """Keyword fallback so labeling never blocks when Jev/LLM are unavailable."""

    def ask(self, state: dict, questions: dict[str, Question]) -> dict[str, Answer]:
        blocks = {item["id"]: item for item in state.get("blocks", [])}
        draft = str(state.get("draft") or "")
        title = str(state.get("title") or "")
        examples = [str(item) for item in state.get("examples") or [] if item]
        answers: dict[str, Answer] = {}
        for key, question in questions.items():
            block = blocks.get(key.split(".", 1)[0], {})
            text = str(block.get("text") or "")
            kind = str(block.get("kind") or "paragraph")
            is_heading = bool(block.get("is_heading"))
            if key.endswith(".role") and isinstance(question, Choice):
                role = infer_role(kind, text, is_heading)
                answers[key] = Answer(value=role, confidence=0.62 if role != "other" else 0.4)
            elif key.endswith(".format_rule") and isinstance(question, Noul):
                answers[key] = Answer(
                    value=bool(PLACEHOLDER_RE.search(text) or "宋体" in text or "字号" in text),
                    confidence=0.7,
                )
            elif key == "kind" and isinstance(question, Choice):
                answers[key] = Answer(value=infer_intent_kind(str(state.get("message") or "")), confidence=0.7)
            elif key == "substantive" and isinstance(question, Noul):
                body = draft.strip()
                answers[key] = Answer(
                    value=len(body) >= 20 and (not title or title not in body or len(body) > len(title) + 12),
                    confidence=0.65,
                )
            elif key == "example_left" and isinstance(question, Noul):
                answers[key] = Answer(
                    value=any(example in draft for example in examples),
                    confidence=0.8,
                )
            elif key == "follows_rules" and isinstance(question, Score):
                left = any(example in draft for example in examples)
                value = question.criteria[0] if left and question.criteria else (
                    question.criteria[-1] if question.criteria and len(draft.strip()) >= 20 else (question.criteria[1] if len(question.criteria) > 1 else "部分遵守")
                )
                answers[key] = Answer(value=value, confidence=0.6)
            elif isinstance(question, Score):
                answers[key] = Answer(value=question.criteria[-1] if question.criteria else "unknown", confidence=0.3)
            else:
                answers[key] = Answer(value=False, confidence=0.3)
        return answers
