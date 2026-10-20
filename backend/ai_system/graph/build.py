"""One research controller: understand → observe → act → commit → observe."""

from langgraph.graph import END, START, StateGraph

from .context import GraphContext
from .state import PaperState


def solve_join(state: PaperState) -> dict:
    """Barrier for parallel workers; recovery also resumes at this boundary."""
    return {}


def build_graph():
    from ..research.control import observe
    from ..research.loop import select, act

    graph = StateGraph(PaperState, context_schema=GraphContext)
    for name, node in (("observe", observe), ("act", act), ("solve_join", solve_join)):
        graph.add_node(name, node)
    graph.add_edge(START, "observe")
    graph.add_conditional_edges("observe", select, ["act", "observe", END])
    graph.add_edge("act", "solve_join")
    graph.add_edge("solve_join", "observe")
    return graph


def compile_graph(checkpointer=None):
    return build_graph().compile(checkpointer=checkpointer)
