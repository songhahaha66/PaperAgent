from __future__ import annotations

from ...judge.questions.draft import draft_questions
from ...schemas.draft import Draft, Judgement
from ...schemas.template_spec import Slot


def judge_draft(draft: Draft, slot: Slot, judge=None) -> Judgement:
    text = "\n".join(
        getattr(block, "text", "")
        or " ".join(getattr(block, "items", []) or [])
        or " ".join(" ".join(row) for row in getattr(block, "rows", []) or [])
        or getattr(block, "caption", "")
        or getattr(block, "latex", "")
        or getattr(block, "ref_id", "")
        for block in draft.blocks
    )
    example_left = any(example and example in text for example in slot.examples)
    substantive = len(text.strip()) >= max(slot.constraints.min_chars, 20)
    follows = "完全遵守" if substantive and not example_left else ("明显违反" if example_left else "部分遵守")
    judgement = Judgement(
        substantive=substantive,
        example_left=example_left,
        follows_rules=follows,
        confidence=0.7 if substantive else 0.4,
        passed=substantive and not example_left,
        reason="内容充足" if substantive and not example_left else "需要补充或清除示例",
    )
    if judge is None:
        return judgement
    try:
        answers = judge.ask(
            {"draft": text, "title": slot.title, "examples": slot.examples, "role": slot.role},
            draft_questions(slot.title or slot.id),
        )
        if "substantive" in answers:
            judgement.substantive = bool(answers["substantive"].value)
        if "example_left" in answers:
            judgement.example_left = bool(answers["example_left"].value) or example_left
        if "follows_rules" in answers and answers["follows_rules"].value:
            judgement.follows_rules = str(answers["follows_rules"].value)
        judgement.confidence = max(
            (getattr(answer, "confidence", 0) or 0) for answer in answers.values()
        ) if answers else judgement.confidence
        judgement.passed = (
            judgement.substantive
            and not judgement.example_left
            and judgement.follows_rules != "明显违反"
        )
        judgement.reason = "judge" if answers else judgement.reason
    except Exception:
        pass
    return judgement
