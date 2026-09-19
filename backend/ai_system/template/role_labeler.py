from __future__ import annotations

from ..judge.heuristic import HeuristicJudge, infer_role
from ..judge.questions.role import role_questions
from ..schemas.template_spec import SlotRole
from .ooxml_parser import ParsedBlock


def label_blocks(
    blocks: list[ParsedBlock],
    judge: HeuristicJudge | None = None,
) -> list[tuple[ParsedBlock, SlotRole, float, str]]:
    judge = judge or HeuristicJudge()
    answers = judge.ask(
        {
            "blocks": [
                {
                    "id": block.id,
                    "text": block.text,
                    "kind": block.kind,
                    "is_heading": block.is_heading,
                }
                for block in blocks
            ]
        },
        role_questions(blocks),
    )
    labeled = []
    for block in blocks:
        answer = answers.get(f"{block.id}.role")
        fallback = infer_role(block.kind, block.text, block.is_heading)
        role = _coerce_role(getattr(answer, "value", None)) or fallback
        confidence = float(getattr(answer, "confidence", 0.55))
        source = "judge" if getattr(answer, "value", None) else "ooxml"
        labeled.append((block, role, confidence, source))
    return labeled


def _coerce_role(value: str | None) -> SlotRole | None:
    allowed = {
        "heading",
        "fixed_text",
        "placeholder_fill",
        "example_delete",
        "instruction_delete",
        "caption",
        "table",
        "figure_slot",
        "other",
    }
    if value in allowed:
        return value  # type: ignore[return-value]
    return None
