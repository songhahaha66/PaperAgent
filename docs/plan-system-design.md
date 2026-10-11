# PaperAgent Plan System Design

## Goal

PaperAgent needs a plan system that behaves like spec-driven coding workflows: plans are explicit, trackable, dynamic, and stable in the UI. The frontend must not depend on arbitrary Markdown tables, because LLM-generated Markdown changes shape across runs and causes the displayed plan to drift.

## Research Summary

Modern spec-driven coding workflows such as Spec Kit and Kiro commonly separate the work into stable stages:

- Requirements: clarify the user goal and constraints.
- Design: decide the architecture or document structure.
- Tasks: break the design into ordered executable work items.
- Implementation: execute tasks while updating state.
- Verification: check artifacts against requirements.

The important product pattern is not the specific wording of a plan, but the durable contract: task identity, status, dependencies, current focus, next actions, and verification state.

## Design

The source of truth is `plan.json`. `plan.md` remains as a compatibility artifact because MainAgent still emits Markdown tables through `update_plan`.

`update_plan` writes both files:

- `plan.md`: human-readable compatibility export.
- `plan.json`: structured dynamic state for frontend rendering, ReviewAgent, and workspace metadata.

ReviewAgent prefers `plan.json`. It falls back to parsing `plan.md` only for old workspaces that have no JSON plan.

## Plan JSON Contract

```json
{
  "version": 1,
  "revision": 1,
  "title": "写作计划",
  "methodology": "spec-driven",
  "planning_mode": "dynamic",
  "phases": [],
  "active_phase": "implement",
  "items": [],
  "stats": {},
  "current_focus": null,
  "next_actions": [],
  "source": "update_plan_markdown",
  "source_markdown": "...",
  "constraints": {},
  "evidence": {},
  "updated_at": "..."
}
```

Each item has stable fields:

- `id`
- `order`
- `title`
- `status`: `pending`, `in_progress`, `completed`, or `blocked`
- `description`
- `phase`: `requirements`, `design`, `tasks`, `implement`, or `verify`
- `depends_on`

Each phase object also carries derived progress:

- `status`
- `item_count`
- `completed_count`

`active_phase` follows `current_focus.phase`. Earlier spec-driven stages are treated as completed once a later stage is active.

## Frontend Rules

- Prefer `plan.json`.
- Fall back to parsing `plan.md` only for old workspaces.
- Render with fixed components: progress, stats, phases, current focus, next actions, and task list.
- Highlight the active / completed / blocked phase. Do not render plan Markdown as the primary UI.

## Dynamic Planning Rules

- The plan is revised over time; `revision` increments with each `update_plan`.
- `current_focus` follows the active item, or the next pending item if no active item exists.
- `next_actions` lists the next pending or blocked tasks.
- Tasks retain stable IDs based on order for compatibility with Markdown input.
- Workspace evidence can reconcile item status, but the rendered contract stays `plan.json`.
