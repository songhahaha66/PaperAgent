from __future__ import annotations

import json
from pathlib import Path

from ...schemas.plan import WRITABLE_ROLES, derive_plan, project_plan_json
from ..state import PaperState


def plan_node(state: PaperState) -> PaperState:
    if state.spec is None:
        return state
    plan = derive_plan(state.spec, state.ir)
    if state.intent.kind == "edit" and state.intent.target_slots:
        allowed = set(state.intent.target_slots)
        for task in plan.tasks:
            if task.slot_id in allowed:
                task.status = "pending"
            elif task.status != "blocked":
                task.status = "committed"
    elif state.intent.kind in {"question", "chat"}:
        for task in plan.tasks:
            if task.status == "pending":
                task.status = "blocked"
    pending = [task.slot_id for task in plan.tasks if task.status == "pending"]
    plan.batches = [pending[index:index + 3] for index in range(0, len(pending), 3)]
    state.plan = plan
    state.current_batch = plan.batches[0] if plan.batches else []
    return state


async def persist_plan(state: PaperState, emit_json) -> dict:
    if state.plan is None:
        return {}
    projected = project_plan_json(state.plan, title="写作计划")
    workspace = Path(state.workspace_dir)
    (workspace / "plan.json").write_text(
        json.dumps(projected, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = ["# 写作计划", "", "| 序号 | 章节 | 状态 | 说明 |", "|---|---|---|---|"]
    for index, task in enumerate(state.plan.tasks, start=1):
        mark = {"pending": "⬜ 待写", "in_progress": "⏳ 进行中", "drafted": "⏳ 进行中", "committed": "✅ 已完成", "blocked": "❌ 阻塞"}[
            task.status
        ]
        writable = task.slot_id
        lines.append(f"| {index} | {task.title} | {mark} | {writable} |")
    (workspace / "plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if emit_json:
        await emit_json("plan_updated", projected)
        await emit_json("file_changed", "plan.json")
    return projected
