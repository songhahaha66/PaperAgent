"""Versioned research control; execution capabilities remain in graph.nodes."""

ENGINE_VERSION = "research-v3"

# One finite backend default shared by the coordinator and atomic reservations:
# literature -> targeted experiment -> convergence, with room for one repair.
DEFAULT_PLANNING_CALL_LIMIT = 4
