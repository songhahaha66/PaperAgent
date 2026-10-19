# Research control

See [the domain architecture and migration contract](../../../ARCHITECTURE.md).

`store.py` owns transactional research state; `loop.py` selects capabilities; `control.py` publishes grounded worker submissions. LangGraph persists execution positions, and `ai_system/modules` owns the research methods and outputs.
