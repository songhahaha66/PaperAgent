from __future__ import annotations

import json
from pathlib import Path

from ai_system.core_tools.docx_styles import extract_style_fingerprint
from config.paths import get_templates_path

from ..judge.heuristic import HeuristicJudge
from ..schemas.template_spec import (
    Block,
    FontHint,
    FormatRule,
    Furniture,
    Slot,
    SlotConstraints,
    TablePreview,
    TemplateSpec,
)
from .ooxml_parser import parse_docx
from .role_labeler import label_blocks

SPEC_FILENAME = "spec.json"
ANALYSIS_DIRNAME = ".analysis"

ROLE_EXPECTS = {
    "heading": [],
    "fixed_text": [],
    "placeholder_fill": ["paragraph", "list", "code"],
    "example_delete": [],
    "instruction_delete": [],
    "caption": ["paragraph"],
    "table": ["table_rows"],
    "figure_slot": ["figure"],
    "other": ["paragraph"],
}


def build_template_spec(
    docx_path: Path | str,
    template_id: int,
    judge: HeuristicJudge | None = None,
) -> TemplateSpec:
    parsed = parse_docx(docx_path)
    labeled = label_blocks(parsed.blocks, judge=judge)
    blocks = [
        Block(
            id=block.id,
            kind=block.kind,  # type: ignore[arg-type]
            style=block.style,
            outline_level=block.outline_level,
            numbered=block.numbered,
            font=FontHint(bold=block.bold, size_pt=block.size_pt),
            text=block.text,
            table=TablePreview(
                rows=len(block.table_rows),
                cols=len(block.table_rows[0]) if block.table_rows else 0,
                cells=block.table_rows[:5],
            )
            if block.kind == "table"
            else None,
        )
        for block, _, _, _ in labeled
    ]

    section_path: list[str] = []
    slots: list[Slot] = []
    format_rules: list[FormatRule] = []
    for block, role, confidence, source in labeled:
        if role == "heading" and block.text:
            level = (block.outline_level or 0) + 1
            section_path = section_path[: max(level - 1, 0)] + [block.text]
        if role == "instruction_delete" and any(token in block.text for token in ("宋体", "字号", "行距", "页边距")):
            format_rules.append(FormatRule(scope="body", font="宋体" if "宋体" in block.text else None))
        slots.append(
            Slot(
                id=_slot_id(role, block.id, section_path),
                role=role,
                anchor_block=block.id,
                section_path=list(section_path),
                expects=list(ROLE_EXPECTS.get(role, ["paragraph"])),
                constraints=SlotConstraints(style=block.style, min_chars=20 if role == "placeholder_fill" else 0),
                confidence=confidence,
                source=source,  # type: ignore[arg-type]
                examples=[block.text] if role == "example_delete" and block.text else [],
                title=block.text[:80] or block.id,
            )
        )

    fingerprint = {}
    try:
        fingerprint = extract_style_fingerprint(Path(docx_path)).to_dict()
    except Exception:
        fingerprint = {}

    return TemplateSpec(
        template_id=template_id,
        version=1,
        blocks=blocks,
        slots=slots,
        format_rules=format_rules,
        style_fingerprint=fingerprint,
        furniture=Furniture(
            has_header=parsed.has_header,
            has_footer=parsed.has_footer,
            image_names=parsed.media_names,
        ),
    )


def persist_template_spec(spec: TemplateSpec, analysis_dir: Path | None = None) -> Path:
    directory = analysis_dir or (get_templates_path() / ANALYSIS_DIRNAME / str(spec.template_id))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / SPEC_FILENAME
    path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_template_spec(template_id: int, analysis_dir: Path | None = None) -> TemplateSpec | None:
    directory = analysis_dir or (get_templates_path() / ANALYSIS_DIRNAME / str(template_id))
    path = directory / SPEC_FILENAME
    if not path.exists():
        return None
    return TemplateSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _slot_id(role: str, block_id: str, section_path: list[str]) -> str:
    if section_path:
        slug = ".".join(_slug(part) for part in section_path[-2:])
        return f"slot.{slug}.{role}.{block_id.lower()}"
    return f"slot.{role}.{block_id.lower()}"


def _slug(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in text)
    return cleaned.strip("-")[:24] or "section"
