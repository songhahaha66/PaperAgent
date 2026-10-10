from __future__ import annotations

from pathlib import Path

from ..schemas.paper_ir import (
    Citation,
    Code,
    Equation,
    FigureRef,
    ListBlock,
    PaperIR,
    Paragraph,
    TableRows,
)
from ..schemas.template_spec import TemplateSpec


def render_markdown(spec: TemplateSpec, ir: PaperIR, output_path: Path | str | None = None) -> str:
    lines: list[str] = []

    for slot in spec.slots:
        if slot.role in {"instruction_delete", "example_delete"}:
            continue
        if slot.role == "heading":
            # Heading depth follows the template outline; the first-level section is "#".
            level = min(max(len(slot.section_path), 1), 6)
            heading = slot.title or (slot.section_path[-1] if slot.section_path else slot.id)
            if heading:
                lines.append(f"{'#' * level} {heading}")
                lines.append("")
            continue
        if slot.role == "fixed_text":
            if slot.title:
                lines.append(slot.title)
                lines.append("")
            continue
        section = ir.sections.get(slot.id)
        if not section:
            continue
        for block in section.blocks:
            if isinstance(block, Paragraph):
                lines.append(block.text)
                lines.append("")
            elif isinstance(block, Code):
                lines.append(f"```{block.language}")
                lines.append(block.text.rstrip())
                lines.append("```")
                lines.append("")
            elif isinstance(block, ListBlock):
                for index, item in enumerate(block.items, start=1):
                    prefix = f"{index}." if block.ordered else "-"
                    lines.append(f"{prefix} {item}")
                lines.append("")
            elif isinstance(block, FigureRef):
                artifact = ir.artifacts.get(block.artifact_id)
                path = artifact.path if artifact else block.artifact_id
                lines.append(f"![{block.caption or block.artifact_id}]({path})")
                if block.caption:
                    lines.append("")
                    lines.append(block.caption)
                lines.append("")
            elif isinstance(block, TableRows):
                if not block.rows:
                    continue
                header = block.rows[0]
                lines.append("| " + " | ".join(header) + " |")
                lines.append("| " + " | ".join("---" for _ in header) + " |")
                for row in block.rows[1:]:
                    lines.append("| " + " | ".join(row) + " |")
                lines.append("")
            elif isinstance(block, Equation):
                lines.append(f"$$\n{block.latex}\n$$")
                lines.append("")
            elif isinstance(block, Citation):
                ref = ir.references.get(block.ref_id)
                lines.append(ref.text if ref else f"[{block.ref_id}]")
                lines.append("")

    text = "\n".join(lines).rstrip() + "\n"
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return text
