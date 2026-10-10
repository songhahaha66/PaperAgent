from __future__ import annotations

import logging
from pathlib import Path

from ...judge.heuristic import HeuristicJudge
from ...judge.questions.intent import needs_figure_question
from ...schemas.template_spec import (
    Block,
    Slot,
    SlotConstraints,
    TemplateSpec,
)
from ...template.spec_builder import build_template_spec, persist_template_spec
from ..state import PaperState
from .render_node import pristine_template

logger = logging.getLogger(__name__)

DEFAULT_TITLES = ["摘要", "引言", "方法", "结果", "结论"]
FIGURE_SECTION_HINTS = ("结果", "实验", "分析", "result", "experiment")
SPEC_FILENAME = "template_spec.json"


def ensure_template_spec(state: PaperState, judge=None) -> PaperState:
    if state.spec and state.spec.slots:
        return state
    workspace = Path(state.workspace_dir)
    template_id = state.template_id or 0
    if state.output_mode == "word":
        template = pristine_template(workspace)
        if template is not None:
            try:
                state.spec = build_template_spec(template, template_id)
                _persist(state.spec, workspace)
                return state
            except Exception as exc:
                logger.warning("解析 Word 模板失败，退回合成大纲: %s", exc)
    titles = _titles_from_markdown(workspace) or DEFAULT_TITLES
    state.spec = synthetic_markdown_spec(
        template_id,
        titles,
        with_figure=needs_figure(state.user_message, judge),
    )
    _persist(state.spec, workspace)
    return state


def needs_figure(message: str, judge=None) -> bool:
    """Ask the judge layer whether the request implies a program-generated figure."""
    judge = judge or HeuristicJudge()
    try:
        answers = judge.ask({"message": message}, needs_figure_question())
        answer = answers.get("needs_figure")
        if answer is not None and getattr(answer, "confidence", 0) >= 0.5:
            return bool(answer.value)
    except Exception:
        pass
    return bool(HeuristicJudge().ask({"message": message}, needs_figure_question())["needs_figure"].value)


def synthetic_markdown_spec(template_id: int, titles: list[str], with_figure: bool = False) -> TemplateSpec:
    blocks: list[Block] = []
    slots: list[Slot] = []
    figure_section = _pick_figure_section(titles) if with_figure else None
    block_index = 0
    for index, title in enumerate(titles):
        heading_id = f"P{block_index:03d}"
        body_id = f"P{block_index + 1:03d}"
        block_index += 2
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
        if index == figure_section:
            figure_id = f"G{index:03d}"
            blocks.append(Block(id=figure_id, kind="figure", text=""))
            slots.append(
                Slot(
                    id=f"slot.{index}.figure.{figure_id.lower()}",
                    role="figure_slot",
                    anchor_block=figure_id,
                    section_path=[title],
                    title=f"{title}图表",
                    expects=["figure"],
                    constraints=SlotConstraints(min_chars=0),
                    source="judge",
                    confidence=0.6,
                )
            )
    return TemplateSpec(template_id=template_id, version=1, blocks=blocks, slots=slots)


def _pick_figure_section(titles: list[str]) -> int:
    for index, title in enumerate(titles):
        lowered = title.lower()
        if any(hint in lowered for hint in FIGURE_SECTION_HINTS):
            return index
    # No obvious results section: put the figure before the last (conclusion) section.
    return max(len(titles) - 2, 0)


def _persist(spec: TemplateSpec, workspace: Path) -> None:
    system = workspace / ".system"
    persist_template_spec(spec, system)
    (system / SPEC_FILENAME).write_text(spec.model_dump_json(indent=2), encoding="utf-8")


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
