from __future__ import annotations

from pathlib import Path

from ..schemas.paper_ir import PaperIR
from ..schemas.template_spec import TemplateSpec
from .content import content_issues
from .fingerprint import fingerprint_issues
from .issues import ValidationIssue
from .skeleton import skeleton_issues
from .visual import visual_issues


def validate_document(
    spec: TemplateSpec,
    ir: PaperIR,
    paper_path: Path | str,
    template_path: Path | str | None = None,
) -> list[ValidationIssue]:
    paper_path = Path(paper_path)
    issues: list[ValidationIssue] = []
    if paper_path.suffix.lower() == ".docx":
        issues.extend(skeleton_issues(spec, paper_path))
        if template_path:
            issues.extend(fingerprint_issues(Path(template_path), paper_path))
        issues.extend(visual_issues(paper_path))
    issues.extend(content_issues(spec, ir, paper_path))
    return issues
