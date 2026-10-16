"""Research graph: analyze/confirm → observe → execute → commit → observe.

The observation loop schedules dependency-ready tasks from research.sqlite;
accepted work continues through consistency review, writing and delivery.
"""
from typing import List, Union

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .context import GraphContext
from .nodes.analyze import analyze
from .nodes.confirm import confirm
from .nodes.consistency import reconcile
from .nodes.design import design
from .nodes.export import export, route_after_render
from .nodes.validate import render, repair, route_after_validate, validate
from .nodes.write import write
from .state import PaperState


def send_designs(state: PaperState) -> List[Send]:
    return [
        Send("design", {
            "subproblem": sp,
            "analysis": state["analysis"],
            "data_profile": state.get("data_profile", ""),
            "feedback": state.get("feedback", ""),
        })
        for sp in state["analysis"]["subproblems"]
    ]


def route_after_analyze(state: PaperState) -> Union[List[Send], str]:
    return send_designs(state) if state.get("plan_ahead") else "observe"


def route_after_confirm(state: PaperState) -> Union[List[Send], str]:
    return send_designs(state) if not state.get("approved", True) else "observe"


def solve_join(state: PaperState) -> dict:
    """Barrier for parallel workers; recovery also resumes at this boundary."""
    return {}


def build_graph():
    from ..research.control import observe, dispatch, execute

    graph = StateGraph(PaperState, context_schema=GraphContext)
    for name, node in (
        ("analyze", analyze), ("design", design), ("confirm", confirm),
        ("observe", observe), ("solve", execute), ("solve_join", solve_join),
        ("reconcile", reconcile), ("write", write), ("validate", validate),
        ("repair", repair), ("render", render), ("export", export),
    ):
        graph.add_node(name, node)

    graph.add_edge(START, "analyze")
    graph.add_conditional_edges("analyze", route_after_analyze, ["design", "observe"])
    graph.add_edge("design", "confirm")
    graph.add_conditional_edges("confirm", route_after_confirm, ["design", "observe"])
    graph.add_conditional_edges("observe", dispatch, ["solve", "reconcile", END])
    graph.add_edge("solve", "solve_join")
    graph.add_edge("solve_join", "observe")
    graph.add_conditional_edges("reconcile", lambda state: "observe" if state.get("research_restart") else "write", ["observe", "write"])
    graph.add_edge("write", "validate")
    graph.add_conditional_edges("validate", route_after_validate, ["repair", "render", "observe", END])
    graph.add_edge("repair", "validate")
    graph.add_conditional_edges("render", route_after_render, ["export", "observe", END])
    graph.add_edge("export", END)
    return graph


def compile_graph(checkpointer=None):
    return build_graph().compile(checkpointer=checkpointer)
