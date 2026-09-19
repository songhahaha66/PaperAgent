from __future__ import annotations

from ..base import Noul, Question, Score


def draft_questions(title: str) -> dict[str, Question]:
    return {
        "substantive": Noul(instructions=f"草稿是否包含针对「{title}」的实质性内容，而不是空话或复述标题？"),
        "example_left": Noul(instructions="草稿是否残留了槽位 examples 里的示例文字？"),
        "follows_rules": Score(
            instructions="草稿对槽位 constraints 的遵守程度",
            criteria=["明显违反", "部分遵守", "完全遵守"],
        ),
    }
