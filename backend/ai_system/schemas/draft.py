from __future__ import annotations

from pydantic import BaseModel, Field

from .paper_ir import BlockContent, Provenance


class Draft(BaseModel):
    slot_id: str
    blocks: list[BlockContent] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)


class Judgement(BaseModel):
    substantive: bool = False
    example_left: bool = False
    follows_rules: str = "部分遵守"
    citation_support: bool | None = None
    confidence: float = 0.0
    passed: bool = False
    reason: str = ""
