# PaperAgent AI System Design

## Goal

PaperAgent generates academic papers through a plan-driven multi-agent loop. The user gives a goal; the system plans, writes, runs code, and verifies artifacts until the workspace contains a finished paper.

## Agents

```
User request
  → MainAgent
       ├─ Markdown: writemd / readmd / get_paper_status / update_plan
       ├─ Word: writer_agent_execute → WriterAgent
       ├─ Data/charts: code_agent_execute → CodeAgent
       └─ After each attempt: ReviewAgent, up to 3 continuations
            ↓
       update_plan
            → plan.md + plan.json
            → FileManager plan board
```

- **MainAgent** orchestrates. It owns planning, tool routing, and the writing loop. In Markdown mode it writes the paper itself. In Word mode it only issues chapter-level goals.
- **WriterAgent** expands those goals into Word document operations. It does not own the plan.
- **CodeAgent** executes Python, produces charts/data under `runs/`, and never writes the paper. Official figures must be promoted with `promote_artifact`.
- **ReviewAgent** decides whether the attempt is complete. It reads `plan.json` first, falls back to `plan.md`, then inspects `paper.md` / `paper.docx`. Deterministic blockers run before any LLM judgment.

## Plan-Driven Loop

MainAgent follows four runtime phases that map onto the spec-driven stages in `plan.json`:

1. Sense workspace state with `get_paper_status`.
2. Write or revise the plan with `update_plan`.
3. Execute one chapter or artifact at a time, updating the plan only at key checkpoints.
4. Let ReviewAgent accept or continue.

The durable UI and review contract is documented in `plan-system-design.md`. `plan.json` is the source of truth; `plan.md` is a compatibility export.

## Workspace Truth

The workspace, not the model transcript, decides whether work is done:

- `PlanReconciler` rebuilds structured state from Markdown input plus local evidence.
- Word template drift, missing body text, and missing figures can block completion.
- `metadata.json` progress and review status stay in sync with the structured plan.

## Output Modes

- **Markdown**: MainAgent writes `paper.md` with section-level updates. Overwrite of a non-empty paper is rejected.
- **Word**: WriterAgent fills `paper.docx` against the uploaded template contract.
- **LaTeX**: not implemented; the workspace falls back to Markdown.
