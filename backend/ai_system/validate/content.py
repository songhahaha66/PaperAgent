from __future__ import annotations

from pathlib import Path

from ..schemas.paper_ir import PaperIR
from ..schemas.template_spec import TemplateSpec
from ..template.ooxml_parser import parse_docx
from .issues import ValidationIssue


def content_issues(spec: TemplateSpec, ir: PaperIR, paper_path: Path) -> list[ValidationIssue]:
    paper_path = Path(paper_path)
    table_count = 0
    if paper_path.suffix.lower() == ".docx":
        parsed = parse_docx(paper_path)
        paper_text = parsed.text
        table_count = parsed.table_count
    else:
        paper_text = paper_path.read_text(encoding="utf-8") if paper_path.exists() else ""
    issues: list[ValidationIssue] = []

    for slot in spec.slots:
        if slot.role == "placeholder_fill":
            body = ir.text_for_slot(slot.id)
            if len(body.strip()) < slot.constraints.min_chars:
                issues.append(
                    ValidationIssue(
                        code="placeholder_too_short",
                        slot_id=slot.id,
                        detail=f"{slot.id} 正文不足 {slot.constraints.min_chars} 字",
                    )
                )
        if slot.role == "example_delete":
            for example in slot.examples:
                if example and example in paper_text:
                    issues.append(
                        ValidationIssue(
                            code="example_left",
                            slot_id=slot.id,
                            detail=f"示例残留: {example[:40]}",
                        )
                    )
        if slot.role == "instruction_delete" and slot.title and slot.title in paper_text:
            issues.append(
                ValidationIssue(
                    code="instruction_left",
                    slot_id=slot.id,
                    detail=f"写作说明残留: {slot.title[:40]}",
                )
            )
        if slot.role == "table":
            if table_count == 0 and paper_path.suffix.lower() == ".docx":
                issues.append(ValidationIssue(code="table_missing", slot_id=slot.id, detail="表格缺失"))
        if slot.role == "figure_slot":
            section = ir.sections.get(slot.id)
            if section:
                for block in section.blocks:
                    artifact_id = getattr(block, "artifact_id", None)
                    if artifact_id and artifact_id not in ir.artifacts:
                        issues.append(
                            ValidationIssue(
                                code="figure_missing",
                                slot_id=slot.id,
                                detail=f"图片产物不存在: {artifact_id}",
                            )
                        )
    return issues
