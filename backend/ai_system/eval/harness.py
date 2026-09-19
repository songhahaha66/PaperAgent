from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..render.docx_renderer import render_docx
from ..render.markdown_renderer import render_markdown
from ..schemas.paper_ir import PaperIR
from ..schemas.template_spec import TemplateSpec
from ..validate.runner import validate_document


@dataclass
class EvalCase:
    name: str
    spec: TemplateSpec
    ir: PaperIR
    template_path: Path | None = None
    output_mode: str = "markdown"


@dataclass
class EvalResult:
    name: str
    issue_count: int
    leftover_examples: int
    style_drift: int
    skeleton_ok: bool
    duration_ms: float = 0
    details: list[str] = field(default_factory=list)


def run_eval_case(case: EvalCase, workdir: Path) -> EvalResult:
    if case.output_mode == "word" and case.template_path:
        paper = workdir / f"{case.name}.docx"
        render_docx(case.spec, case.ir, case.template_path, paper)
        issues = validate_document(case.spec, case.ir, paper, case.template_path)
    else:
        paper = workdir / f"{case.name}.md"
        render_markdown(case.spec, case.ir, paper)
        issues = validate_document(case.spec, case.ir, paper)
    leftover = sum(1 for issue in issues if issue.code in {"example_left", "instruction_left"})
    drift = sum(1 for issue in issues if issue.code == "style_drift")
    skeleton_ok = all(issue.code != "skeleton_mismatch" for issue in issues)
    return EvalResult(
        name=case.name,
        issue_count=len(issues),
        leftover_examples=leftover,
        style_drift=drift,
        skeleton_ok=skeleton_ok,
        details=[f"{issue.code}:{issue.detail}" for issue in issues],
    )


def summarize(results: list[EvalResult]) -> dict:
    total = len(results) or 1
    return {
        "cases": len(results),
        "clean": sum(1 for item in results if item.issue_count == 0),
        "skeleton_ok_rate": round(sum(1 for item in results if item.skeleton_ok) / total, 3),
        "leftover_examples": sum(item.leftover_examples for item in results),
        "style_drift": sum(item.style_drift for item in results),
    }
