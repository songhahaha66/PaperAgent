# PaperAgent contributor instructions

Use UV for Python dependencies and commands. Read [ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing research behavior.

- Write project documentation in `docs/`. Do not commit or push changes unless the user explicitly requests it.

- Backend: FastAPI, SQLAlchemy and LangGraph. Run `cd backend && uv sync && uv run main.py`; tests: `uv run pytest tests`.
- Frontend: Vue 3, TypeScript, TDesign, Pinia and Vite. Run `cd frontend && npm ci && npm run dev`; checks: `npm run test:workspace && npm run build`.
- All startup paths run Alembic and the workspace cutover before serving requests. Do not add `create_all` or runtime legacy fallbacks.
- `ai_system/research/store.py` owns schema 5 business state. `research/loop.py` selects actions. `graph/` owns checkpoints, confirmation and infrastructure. `modules/` owns research capabilities and skills.
- The research Agent owns drafting through `writing.edit`; delivery only validates, renders and exports the current files. Do not restore independent section-writing or automatic model repair loops. Main and delegated sessions use stable threads in the existing graph database; tool receipts reconcile execution before replay. Context is bounded by tokens with recoverable summaries and on-demand full output. Delegation is optional, scoped, and supports isolated or inherited context; no recursive workers or separate model budgets.
- The default research path uses one sustained Agent and one persistent Python kernel to read original materials, compute and answer. Ordinary calculation does not dispatch a child solver. Method comparisons use on-demand skills and the same generic execution tools; do not restore a CSV-specific dispatcher. All model requests share ResearchRuntime; do not restore local planning, step, attempt or comparison caps. File transfer retries must reuse saved runs without replaying computation.
- All capabilities register through `modules/registry.py`, with input and product contracts. Do not add specialized research routes around the state loop.
- Execution records and result files retain actual code, inputs, output and file hashes. Research reports use values and file paths directly; do not add evidence-registration tools, named-metric acceptance gates or compulsory result IDs. Necessary domain checks run in calculation code; semantic review is advisory. Historical records remain labelled and readable.
- Research topics are optional run grouping metadata. Do not add per-question answer registration, coverage/completeness states, or an answer-update tool; answers and limitations belong in conversation and manuscript. The research tab and its entry points are retired.
- Content feedback and runtime faults are separate durable collections. Historical records with uncertain meaning retain original content and source; do not infer scientific status.
- Personal experience belongs to the authenticated user. Generated summaries are candidates. Source validation and explicit adoption precede retrieval; project attempts freeze actual experience versions. New versions require validation again.
- Preserve the tabbed workspace and split-pane interactions. Desktop initially opens chat and paper side by side; mobile starts with chat. Chat stays pinned. Dragging tabs forms two adjustable panes, and moving them back merges panes. Narrow screens stack panes. User-scoped `workPanels` stores tabs, layout, drafts and reading positions.
- Responsive breakpoint logic lives in `frontend/src/composables/useBreakpoint.ts`.
- Implement interface changes directly in this project and preview the actual app. Do not create standalone visualization prototypes unless the user explicitly requests one.
