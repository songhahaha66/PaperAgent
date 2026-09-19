from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from ...llm import load_prompt
from ...schemas.draft import Draft
from ...schemas.paper_ir import FigureRef, Paragraph, Provenance, TableRows
from ...schemas.template_spec import Slot
from ..state import PaperState

PROMPT_VERSION = "draft-v2"
LLM_ATTEMPTS = 2
IMAGE_MIMES = {"image/png", "image/jpeg", "image/svg+xml"}


class DraftError(RuntimeError):
    """Raised when a model was available but produced no usable draft."""


def heuristic_draft(slot: Slot, state: PaperState) -> Draft:
    provenance = Provenance(model="heuristic", prompt_version=PROMPT_VERSION, run_id=state.run_id)
    if slot.role == "table":
        return Draft(
            slot_id=slot.id,
            blocks=[
                TableRows(
                    table_id=slot.anchor_block,
                    rows=[["项目", "结果"], [slot.title or "指标", "已按模板填写"]],
                )
            ],
            provenance=provenance,
        )
    if slot.role == "figure_slot":
        figures = _image_artifacts(state)
        if not figures:
            raise DraftError(f"{slot.id}: 没有可用的图片产物，无法填充图槽位")
        return Draft(
            slot_id=slot.id,
            blocks=[FigureRef(artifact_id=figures[0].id, caption=slot.title or figures[0].id)],
            provenance=provenance,
        )
    body = (
        f"{slot.title or slot.id}。根据用户需求「{_clip(state.user_message, 80)}」"
        f"说明该部分的背景、步骤和结论，并与模板约束保持一致。"
    )
    min_chars = max(slot.constraints.min_chars, 40)
    while len(body) < min_chars:
        body += " 内容覆盖实验条件、操作要点与可复核的结果描述。"
    return Draft(slot_id=slot.id, blocks=[Paragraph(text=body)], provenance=provenance)


async def draft_slot(slot: Slot, state: PaperState, ctx, feedback: str = "") -> Draft:
    """Produce a Draft for one slot.

    Resolution order: injected writer → writer/main LLM (with schema-error retry) →
    heuristic filler. The heuristic path is only used when no model is configured;
    when a model exists but fails, DraftError is raised so the caller can leave the
    slot pending instead of committing placeholder text into the paper.
    """
    if ctx.writer:
        result = ctx.writer(slot, state, ctx)
        if hasattr(result, "__await__"):
            result = await result
        return result
    llm = ctx.writer_llm or ctx.llm
    if llm is None:
        return heuristic_draft(slot, state)
    return await _llm_draft(slot, state, llm, feedback=feedback)


async def _llm_draft(slot: Slot, state: PaperState, llm, feedback: str = "") -> Draft:
    prompt = _build_prompt(slot, state, feedback)
    last_error = ""
    for attempt in range(LLM_ATTEMPTS):
        message = prompt if not last_error else f"{prompt}\n\n上一次输出无法解析，请修正后只输出 JSON：{last_error}"
        try:
            response = await llm.ainvoke([HumanMessage(content=message)])
        except Exception as exc:
            last_error = f"模型调用失败: {exc}"
            continue
        text = _message_text(response)
        try:
            payload = json.loads(_extract_json(text))
            draft = Draft.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            last_error = str(exc)[:300]
            continue
        draft.slot_id = slot.id
        draft.blocks = [block for block in draft.blocks if _block_has_content(block)]
        if not draft.blocks:
            last_error = "blocks 为空"
            continue
        draft.provenance = Provenance(
            model=_model_name(llm),
            prompt_version=PROMPT_VERSION,
            run_id=state.run_id,
        )
        return draft
    raise DraftError(f"{slot.id}: {last_error or '模型未返回可用草稿'}")


def _build_prompt(slot: Slot, state: PaperState, feedback: str) -> str:
    return load_prompt("draft.md").format(
        slot_id=slot.id,
        title=slot.title or slot.id,
        role=slot.role,
        expects=", ".join(slot.expects) if slot.expects else "paragraph",
        constraints=slot.constraints.model_dump_json(),
        section=" / ".join(slot.section_path),
        user_message=state.user_message,
        neighbors=_neighbor_summary(state, slot.id) or "无",
        history=_history_text(state),
        artifacts=_artifact_summary(state) or "无",
        feedback=feedback.strip() or "无",
    )


def _message_text(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content or "")


def _model_name(llm) -> str:
    for attr in ("model_name", "model", "deployment_name"):
        value = getattr(llm, attr, None)
        if isinstance(value, str) and value:
            return value
    return type(llm).__name__


def _block_has_content(block) -> bool:
    if isinstance(block, Paragraph):
        return bool(block.text.strip())
    if isinstance(block, TableRows):
        return bool(block.rows)
    if isinstance(block, FigureRef):
        return bool(block.artifact_id)
    items = getattr(block, "items", None)
    if items is not None:
        return any(str(item).strip() for item in items)
    text = getattr(block, "text", None) or getattr(block, "latex", None) or getattr(block, "ref_id", None)
    return bool(text and str(text).strip())


def _image_artifacts(state: PaperState):
    if not state.ir:
        return []
    return [artifact for artifact in state.ir.artifacts.values() if artifact.mime in IMAGE_MIMES]


def _artifact_summary(state: PaperState) -> str:
    if not state.ir or not state.ir.artifacts:
        return ""
    return "\n".join(
        f"- artifact_id={artifact.id} ({artifact.mime}) path={artifact.path}"
        for artifact in list(state.ir.artifacts.values())[:12]
    )


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
