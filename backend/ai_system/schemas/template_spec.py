from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


BlockKind = Literal["paragraph", "table", "figure", "sdt"]
BlockType = Literal["paragraph", "code", "list", "figure", "table_rows", "equation", "citation"]
SlotRole = Literal[
    "heading",
    "fixed_text",
    "placeholder_fill",
    "example_delete",
    "instruction_delete",
    "caption",
    "table",
    "figure_slot",
    "other",
]
SLOT_ROLE_VALUES = {
    "heading",
    "fixed_text",
    "placeholder_fill",
    "example_delete",
    "instruction_delete",
    "caption",
    "table",
    "figure_slot",
    "other",
}


class FontHint(BaseModel):
    bold: bool | None = None
    size_pt: float | None = None
    name: str | None = None


class TablePreview(BaseModel):
    rows: int = 0
    cols: int = 0
    cells: list[list[str]] = Field(default_factory=list)


class Block(BaseModel):
    id: str
    kind: BlockKind
    style: str | None = None
    outline_level: int | None = None
    numbered: bool = False
    font: FontHint | None = None
    text: str = ""
    table: TablePreview | None = None


class SlotConstraints(BaseModel):
    style: str | None = None
    min_chars: int = 20
    language: str | None = None
    code_style: str | None = None
    caption_style: str | None = None


class Slot(BaseModel):
    id: str
    role: SlotRole
    anchor_block: str
    section_path: list[str] = Field(default_factory=list)
    expects: list[BlockType] = Field(default_factory=lambda: ["paragraph"])
    constraints: SlotConstraints = Field(default_factory=SlotConstraints)
    confidence: float = 0.5
    source: Literal["ooxml", "judge", "human"] = "ooxml"
    examples: list[str] = Field(default_factory=list)
    title: str = ""


class FormatRule(BaseModel):
    scope: str
    font: str | None = None
    size_pt: float | None = None
    line_spacing: float | None = None
    align: str | None = None


class Furniture(BaseModel):
    has_header: bool = False
    has_footer: bool = False
    image_names: list[str] = Field(default_factory=list)


class TemplateSpec(BaseModel):
    template_id: int
    version: int = 1
    blocks: list[Block] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    format_rules: list[FormatRule] = Field(default_factory=list)
    style_fingerprint: dict = Field(default_factory=dict)
    furniture: Furniture = Field(default_factory=Furniture)
