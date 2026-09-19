from __future__ import annotations

from pathlib import Path

from .issues import ValidationIssue


def visual_issues(_paper_path: Path) -> list[ValidationIssue]:
    """Optional vision pass; Phase 1 keeps this as a no-op hook."""
    return []
