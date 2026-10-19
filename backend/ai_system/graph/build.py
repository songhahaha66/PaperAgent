"""Research graph: analyze/confirm → observe → execute → commit → observe.

The observation loop schedules dependency-ready tasks from research.sqlite;
accepted work continues through consistency review, writing and delivery.
"""
from typing import List, Union

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .context import GraphContext
from ai_system.graph.confirm import confirm
from .state import PaperState


def send_designs(state: PaperState) -> List[Send]:
    return [
        Send("design", {
            "source": state["task"],
            "subproblem": sp,
            "analysis": state["analysis"],
            "data_profile": state.get("data_profile", ""),
            "feedback": state.get("confirmation_feedback", ""),
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


async def analyze_capability(state, runtime):
    from ai_system.modules.registry import invoke
    return await invoke(runtime.context, 'experiments.analyze', state=state)


async def design_capability(branch, runtime):
    from ai_system.modules.registry import invoke
    return await invoke(runtime.context, 'experiments.plan', branch=branch)


def build_graph():
    from ..research.control import observe
    from ..research.loop import select, act

    graph = StateGraph(PaperState, context_schema=GraphContext)
    for name, node in (("analyze", analyze_capability), ("design", design_capability), ("confirm", confirm),
                       ("observe", observe), ("act", act), ("solve_join", solve_join)):
        graph.add_node(name, node)
    graph.add_edge(START, "analyze")
    graph.add_conditional_edges("analyze", route_after_analyze, ["design", "observe"])
    graph.add_edge("design", "confirm")
    graph.add_conditional_edges("confirm", route_after_confirm, ["design", "observe"])
    graph.add_conditional_edges("observe", select, ["act", END])
    graph.add_edge("act", "solve_join")
    graph.add_edge("solve_join", "observe")
    return graph


def compile_graph(checkpointer=None):
    return build_graph().compile(checkpointer=checkpointer)
