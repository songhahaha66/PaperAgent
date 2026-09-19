from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..core_tools.docx_images import inventory_docx_images
from ..schemas.paper_ir import FigureRef, PaperIR
from ..schemas.template_spec import TemplateSpec
from .issues import ValidationIssue


def visual_issues(
    paper_path: Path | str,
    spec: TemplateSpec | None = None,
    ir: PaperIR | None = None,
) -> list[ValidationIssue]:
    paper_path = Path(paper_path)
    if paper_path.suffix.lower() != ".docx" or not paper_path.exists():
        return []
    issues: list[ValidationIssue] = []
    images = inventory_docx_images(paper_path)
    filenames = {item.filename for item in images}

    if spec and spec.furniture.image_names:
        for name in spec.furniture.image_names:
            if name and name not in filenames:
                issues.append(
                    ValidationIssue(
                        code="furniture_missing",
                        detail=f"模板家具图缺失: {name}",
                        severity="error",
                    )
                )

    if ir:
        for slot_id, section in ir.sections.items():
            for block in section.blocks:
                if not isinstance(block, FigureRef):
                    continue
                artifact = ir.artifacts.get(block.artifact_id)
                if artifact is None:
                    issues.append(
                        ValidationIssue(
                            code="figure_missing",
                            slot_id=slot_id,
                            detail=f"图片产物不存在: {block.artifact_id}",
                        )
                    )
                    continue
                artifact_path = Path(artifact.path)
                if not artifact_path.is_absolute():
                    artifact_path = paper_path.parent / artifact.path
                if not artifact_path.exists():
                    issues.append(
                        ValidationIssue(
                            code="figure_missing",
                            slot_id=slot_id,
                            detail=f"图片文件不存在: {artifact.path}",
                        )
                    )
                elif artifact_path.name not in filenames and not any(
                    artifact_path.stem in name for name in filenames
                ):
                    issues.append(
                        ValidationIssue(
                            code="figure_not_embedded",
                            slot_id=slot_id,
                            detail=f"图片未嵌入文档: {artifact.path}",
                            severity="warning",
                        )
                    )

    issues.extend(_pdf_page_issues(paper_path))
    return issues


def _pdf_page_issues(paper_path: Path) -> list[ValidationIssue]:
    if shutil.which("soffice") is None:
        return []
    try:
        from ai_system.docx_skill.scripts.office.soffice import run_soffice
    except Exception:
        return []
    workdir = Path(tempfile.mkdtemp(prefix="pa-visual-"))
    try:
        result = run_soffice(
            ["--headless", "--convert-to", "pdf", "--outdir", str(workdir), str(paper_path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        pdfs = list(workdir.glob("*.pdf"))
        if result.returncode != 0 or not pdfs:
            return []
        if pdfs[0].stat().st_size < 64:
            return [ValidationIssue(code="visual_empty_page", detail="导出的 PDF 几乎为空", severity="warning")]
    except Exception:
        return []
    return []
