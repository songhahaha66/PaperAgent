from __future__ import annotations

from pathlib import Path

from ai_system.core_tools.docx_styles import compare_docx_styles

from .issues import ValidationIssue


def fingerprint_issues(template_path: Path, paper_path: Path) -> list[ValidationIssue]:
    try:
        diffs = compare_docx_styles(template_path, paper_path)
    except Exception as exc:
        return [ValidationIssue(code="fingerprint_error", detail=str(exc), severity="warning")]
    return [
        ValidationIssue(code="style_drift", detail=diff, severity="error")
        for diff in diffs
    ]
