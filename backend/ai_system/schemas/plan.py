from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .paper_ir import PaperIR
from .template_spec import Slot, TemplateSpec


class TaskNeeds(BaseModel):
    code: bool = False
    search: bool = False
    figure: bool = False


class Task(BaseModel):
    slot_id: str
    title: str
    needs: TaskNeeds = Field(default_factory=TaskNeeds)
    depends_on: list[str] = Field(default_factory=list)
    status: Literal["pending", "in_progress", "drafted", "committed", "blocked"] = "pending"


class Plan(BaseModel):
    tasks: list[Task] = Field(default_factory=list)
    batches: list[list[str]] = Field(default_factory=list)


WRITABLE_ROLES = {"placeholder_fill", "table", "figure_slot", "caption"}
STATUS_MAP = {
    "pending": "pending",
    "in_progress": "in_progress",
    "drafted": "in_progress",
    "committed": "completed",
    "blocked": "blocked",
}


def derive_plan(spec: TemplateSpec, ir: PaperIR | None = None) -> Plan:
    writable = [slot for slot in spec.slots if slot.role in WRITABLE_ROLES]
    tasks: list[Task] = []
    previous_id: str | None = None
    for slot in writable:
        status: Literal["pending", "in_progress", "drafted", "committed", "blocked"] = "pending"
        if ir and slot.id in ir.sections and ir.text_for_slot(slot.id).strip():
            status = "committed"
        title = slot.title or (slot.section_path[-1] if slot.section_path else slot.id)
        tasks.append(
            Task(
                slot_id=slot.id,
                title=title,
                needs=TaskNeeds(figure=slot.role == "figure_slot", code="code" in slot.expects),
                depends_on=[previous_id] if previous_id else [],
                status=status,
            )
        )
        previous_id = slot.id

    pending_ids = [task.slot_id for task in tasks if task.status == "pending"]
    batches = [pending_ids[index:index + 3] for index in range(0, len(pending_ids), 3)]
    return Plan(tasks=tasks, batches=batches)


def project_plan_json(plan: Plan, title: str = "写作计划") -> dict:
    items = []
    for index, task in enumerate(plan.tasks, start=1):
        ui_status = STATUS_MAP[task.status]
        items.append(
            {
                "id": task.slot_id,
                "order": index,
                "title": task.title,
                "status": ui_status,
                "status_label": {
                    "pending": "待写",
                    "in_progress": "进行中",
                    "completed": "已完成",
                    "blocked": "阻塞",
                }[ui_status],
                "description": "",
                "phase": "implement",
                "depends_on": task.depends_on,
            }
        )
    stats = {
        "total": len(items),
        "completed": sum(1 for item in items if item["status"] == "completed"),
        "in_progress": sum(1 for item in items if item["status"] == "in_progress"),
        "blocked": sum(1 for item in items if item["status"] == "blocked"),
        "pending": sum(1 for item in items if item["status"] == "pending"),
    }
    stats["progress_percent"] = round((stats["completed"] / stats["total"]) * 100) if stats["total"] else 0
    current = next((item for item in items if item["status"] == "in_progress"), None)
    if current is None:
        current = next((item for item in items if item["status"] == "pending"), None)
    return {
        "version": 1,
        "revision": 1,
        "title": title,
        "methodology": "spec-driven",
        "planning_mode": "dynamic",
        "phases": [
            {"id": "requirements", "title": "需求澄清"},
            {"id": "design", "title": "方案设计"},
            {"id": "tasks", "title": "任务拆解"},
            {"id": "implement", "title": "执行生成"},
            {"id": "verify", "title": "验收检查"},
        ],
        "active_phase": "implement",
        "items": items,
        "stats": stats,
        "current_focus": current,
        "next_actions": [
            {"id": item["id"], "title": item["title"], "reason": "等待执行"}
            for item in items
            if item["status"] in {"pending", "blocked"}
        ][:3],
        "source": "template_spec",
        "updated_at": datetime.now().isoformat(),
    }
