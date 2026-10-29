"""Versioned research control; execution capabilities remain in graph.nodes."""

ENGINE_VERSION = "research-v3"

# Main-loop decisions share the existing overall request budget and operation
# guards. An explicitly configured planning limit remains supported.
DEFAULT_PLANNING_CALL_LIMIT = None
