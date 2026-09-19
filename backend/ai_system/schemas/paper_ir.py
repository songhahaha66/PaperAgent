from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class Paragraph(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    text: str


class Code(BaseModel):
    type: Literal["code"] = "code"
    language: str = "text"
    text: str


class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    ordered: bool = False
    items: list[str] = Field(default_factory=list)


class FigureRef(BaseModel):
    type: Literal["figure"] = "figure"
    artifact_id: str
    caption: str = ""


class TableRows(BaseModel):
    type: Literal["table_rows"] = "table_rows"
    table_id: str
    rows: list[list[str]] = Field(default_factory=list)


class Equation(BaseModel):
    type: Literal["equation"] = "equation"
    latex: str


class Citation(BaseModel):
    type: Literal["citation"] = "citation"
    ref_id: str


BlockContent = Annotated[
    Union[Paragraph, Code, ListBlock, FigureRef, TableRows, Equation, Citation],
    Field(discriminator="type"),
]


class Provenance(BaseModel):
    model: str = ""
    prompt_version: str = ""
    run_id: str = ""
    judged_by: str = ""


class SectionContent(BaseModel):
    slot_id: str
    blocks: list[BlockContent] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    revision: int = 1


class Artifact(BaseModel):
    id: str
    path: str
    mime: str = "application/octet-stream"
    produced_by: str = ""


class Reference(BaseModel):
    id: str
    text: str
    url: str | None = None


class PaperIR(BaseModel):
    work_id: str
    revision: int = 1
    sections: dict[str, SectionContent] = Field(default_factory=dict)
    artifacts: dict[str, Artifact] = Field(default_factory=dict)
    references: dict[str, Reference] = Field(default_factory=dict)

    def text_for_slot(self, slot_id: str) -> str:
        section = self.sections.get(slot_id)
        if not section:
            return ""
        parts: list[str] = []
        for block in section.blocks:
            if isinstance(block, Paragraph):
                parts.append(block.text)
            elif isinstance(block, Code):
                parts.append(block.text)
            elif isinstance(block, ListBlock):
                parts.extend(block.items)
            elif isinstance(block, FigureRef):
                parts.append(block.caption)
            elif isinstance(block, TableRows):
                parts.extend(" ".join(row) for row in block.rows)
            elif isinstance(block, Equation):
                parts.append(block.latex)
            elif isinstance(block, Citation):
                parts.append(block.ref_id)
        return "\n".join(part for part in parts if part)
