# PaperAgent contributor instructions

Use UV for Python dependencies and commands. Read [ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing research behavior.

- Backend: FastAPI, SQLAlchemy and LangGraph. Run `cd backend && uv sync && uv run main.py`; tests: `uv run pytest tests`.
- Frontend: Vue 3, TypeScript, TDesign, Pinia and Vite. Run `cd frontend && npm ci && npm run dev`; checks: `npm run test:workspace && npm run build`.
- All startup paths run Alembic and the workspace cutover before serving requests. Do not add `create_all` or runtime legacy fallbacks.
- `ai_system/research/store.py` owns schema 5 business state. `research/loop.py` selects actions. `graph/` owns checkpoints, confirmation and infrastructure. `modules/` owns research capabilities and skills.
- All capabilities register through `modules/registry.py`, with input and product contracts. Do not add specialized research routes around the state loop.
- Numeric and figure claims retain successful kernel provenance; matching stdout numbers does not establish meaning. Current evidence and interpretation enter publication; historical/unsupported views must be explicitly labelled. Semantic review is advisory and never an automatic publication gate.
- Content feedback and runtime faults are separate durable collections. Historical records with uncertain meaning retain original content and source; do not infer scientific status.
- Personal experience belongs to the authenticated user. Generated summaries are candidates. Source validation and explicit adoption precede retrieval; project attempts freeze actual experience versions. New versions require validation again.
- Preserve the tabbed workspace and split-pane interactions. Desktop initially opens chat and paper side by side; mobile starts with chat. Chat stays pinned. Dragging tabs forms two adjustable panes, and moving them back merges panes. Narrow screens stack panes. User-scoped `workPanels` stores tabs, layout, drafts and reading positions.
- Responsive breakpoint logic lives in `frontend/src/composables/useBreakpoint.ts`.
- Implement interface changes directly in this project and preview the actual app. Do not create standalone visualization prototypes unless the user explicitly requests one.
