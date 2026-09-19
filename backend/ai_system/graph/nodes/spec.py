from __future__ import annotations

from pathlib import Path

from ...schemas.template_spec import (
    Block,
    Slot,
    SlotConstraints,
    TemplateSpec,
)
from ...template.spec_builder import build_template_spec, persist_template_spec
from ..state import PaperState

DEFAULT_TITLES = ["摘要", "引言", "方法", "结果", "结论"]


def ensure_template_spec(state: PaperState) -> PaperState:
    if state.spec and state.spec.slots:
        return state
    workspace = Path(state.workspace_dir)
    template_id = state.template_id or 0
    if state.output_mode == "word":
        for candidate in (
            workspace / ".system" / "_template_original.docx",
            workspace / "paper.docx",
        ):
            if candidate.exists():
                try:
                    state.spec = build_template_spec(candidate, template_id)
                    persist_template_spec(state.spec, workspace / ".system")
                    (workspace / ".system" / "template_spec.json").write_text(
                        state.spec.model_dump_json(indent=2), encoding="utf-8"
                    )
                    return state
                except Exception:
                    continue
    state.spec = synthetic_markdown_spec(template_id, _titles_from_markdown(workspace) or DEFAULT_TITLES)
    persist_template_spec(state.spec, workspace / ".system")
    (workspace / ".system" / "template_spec.json").write_text(
        state.spec.model_dump_json(indent=2), encoding="utf-8"
    )
    return state


def synthetic_markdown_spec(template_id: int, titles: list[str]) -> TemplateSpec:
    blocks: list[Block] = []
    slots: list[Slot] = []
    for index, title in enumerate(titles):
        heading_id = f"P{index * 2:03d}"
        body_id = f"P{index * 2 + 1:03d}"
        blocks.append(Block(id=heading_id, kind="paragraph", text=title, outline_level=0))
        blocks.append(Block(id=body_id, kind="paragraph", text=f"请在此处填写{title}"))
        slots.append(
            Slot(
                id=f"slot.{index}.heading.{heading_id.lower()}",
                role="heading",
                anchor_block=heading_id,
                section_path=[title],
                title=title,
                source="ooxml",
                confidence=0.9,
            )
        )
        slots.append(
            Slot(
                id=f"slot.{index}.body.{body_id.lower()}",
                role="placeholder_fill",
                anchor_block=body_id,
                section_path=[title],
                title=title,
                expects=["paragraph", "list", "code"],
                constraints=SlotConstraints(min_chars=40),
                source="ooxml",
                confidence=0.85,
            )
        )
    return TemplateSpec(template_id=template_id, version=1, blocks=blocks, slots=slots)


def _titles_from_markdown(workspace: Path) -> list[str]:
    paper = workspace / "paper.md"
    if not paper.exists():
        return []
    titles = []
    for line in paper.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            if title:
                titles.append(title)
    return titles[:12]
