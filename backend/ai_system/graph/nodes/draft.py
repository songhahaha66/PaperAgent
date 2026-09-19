from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage

from ...llm import load_prompt
from ...schemas.draft import Draft
from ...schemas.paper_ir import Paragraph, Provenance, TableRows
from ...schemas.template_spec import Slot
from ..state import PaperState


def heuristic_draft(slot: Slot, state: PaperState) -> Draft:
    if slot.role == "table":
        return Draft(
            slot_id=slot.id,
            blocks=[
                TableRows(
                    table_id=slot.anchor_block,
                    rows=[["项目", "结果"], [slot.title or "指标", "已按模板填写"]],
                )
            ],
            provenance=Provenance(model="heuristic", prompt_version="draft-v1", run_id=state.run_id, judged_by=""),
        )
    body = (
        f"{slot.title or slot.id}。根据用户需求「{_clip(state.user_message, 80)}」"
        f"说明该部分的背景、步骤和结论，并与模板约束保持一致。"
    )
    min_chars = max(slot.constraints.min_chars, 40)
    while len(body) < min_chars:
        body += " 内容覆盖实验条件、操作要点与可复核的结果描述。"
    return Draft(
        slot_id=slot.id,
        blocks=[Paragraph(text=body)],
        provenance=Provenance(model="heuristic", prompt_version="draft-v1", run_id=state.run_id),
    )


async def draft_slot(slot: Slot, state: PaperState, ctx) -> Draft:
    if ctx.writer:
        result = ctx.writer(slot, state, ctx)
        if hasattr(result, "__await__"):
            result = await result
        return result
    llm = ctx.writer_llm or ctx.llm
    if llm is not None:
        try:
            return await _llm_draft(slot, state, llm)
        except Exception:
            pass
    return heuristic_draft(slot, state)


async def _llm_draft(slot: Slot, state: PaperState, llm) -> Draft:
    prompt = load_prompt("draft.md").format(
        slot_id=slot.id,
        title=slot.title or slot.id,
        role=slot.role,
        constraints=slot.constraints.model_dump_json(),
        section=" / ".join(slot.section_path),
        user_message=state.user_message,
        neighbors=_neighbor_summary(state, slot.id),
        history=_history_text(state),
    )
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    text = getattr(response, "content", "") or ""
    payload = json.loads(_extract_json(text))
    draft = Draft.model_validate(payload)
    draft.slot_id = slot.id
    if not draft.blocks:
        return heuristic_draft(slot, state)
    return draft


def _history_text(state: PaperState) -> str:
    if not state.history:
        return "无"
    return "\n".join(
        f"{turn.get('role')}: {_clip(turn.get('content') or '', 160)}" for turn in state.history[-8:]
    )


def _neighbor_summary(state: PaperState, slot_id: str) -> str:
    if not state.ir:
        return ""
    parts = []
    for key, section in list(state.ir.sections.items())[:4]:
        if key == slot_id:
            continue
        parts.append(f"{key}: {state.ir.text_for_slot(key)[:80]}")
    return "\n".join(parts)


def _extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _clip(text: str, limit: int) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"
