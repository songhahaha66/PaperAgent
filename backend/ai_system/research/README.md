# Research engine

`store.py` owns schema 5 state. `planner.py` chooses research decisions; `loop.py` dispatches through LangGraph and preserves `solve_join`. Experiments execute continuous CodeAct actions and return complete findings. Interpretation edits never run calculation. Read views project the same questions, reports, results, claims and history.

See [ARCHITECTURE.md](../../../ARCHITECTURE.md) for ownership, versioning, migration and advisory checks.
