from __future__ import annotations

from pathlib import Path

from ..template.ooxml_parser import parse_docx
from ..schemas.template_spec import TemplateSpec
from .issues import ValidationIssue


def skeleton_issues(spec: TemplateSpec, paper_path: Path) -> list[ValidationIssue]:
    parsed = parse_docx(paper_path)
    paper_headings = [text for _, text in parsed.headings]
    expected = [slot.title for slot in spec.slots if slot.role == "heading" and slot.title]
    issues: list[ValidationIssue] = []
    if paper_headings != expected:
        issues.append(
            ValidationIssue(
                code="skeleton_mismatch",
                detail=f"标题骨架不一致：期望 {expected}，实际 {paper_headings}",
            )
        )
    for slot in spec.slots:
        if slot.role != "fixed_text" or not slot.title:
            continue
        if slot.title not in parsed.text:
            issues.append(
                ValidationIssue(
                    code="fixed_text_missing",
                    slot_id=slot.id,
                    detail=f"固定文字缺失: {slot.title}",
                )
            )
    return issues
