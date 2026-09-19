from __future__ import annotations

from ...judge.heuristic import HeuristicJudge
from ...schemas.draft import Draft, Judgement
from ...schemas.template_spec import Slot


def judge_draft(draft: Draft, slot: Slot, judge=None) -> Judgement:
    text = "\n".join(
        getattr(block, "text", "")
        or " ".join(getattr(block, "items", []) or [])
        or " ".join(" ".join(row) for row in getattr(block, "rows", []) or [])
        or getattr(block, "caption", "")
        for block in draft.blocks
    )
    substantive = len(text.strip()) >= max(slot.constraints.min_chars, 20)
    example_left = any(example and example in text for example in slot.examples)
    follows = "完全遵守" if substantive and not example_left else ("明显违反" if example_left else "部分遵守")
    judgement = Judgement(
        substantive=substantive,
        example_left=example_left,
        follows_rules=follows,
        confidence=0.7 if substantive else 0.4,
        passed=substantive and not example_left,
        reason="内容充足" if substantive and not example_left else "需要补充或清除示例",
    )
    if judge and not isinstance(judge, HeuristicJudge):
        try:
            answers = judge.ask({"draft": text, "title": slot.title}, {})
            if "substantive" in answers:
                judgement.substantive = bool(answers["substantive"].value)
            judgement.passed = judgement.substantive and not judgement.example_left
        except Exception:
            pass
    return judgement
