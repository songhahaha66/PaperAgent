"""
数模论文状态图

    analyze ─┬─(需要人工确认)─Send×N──▶ design ──▶ confirm ─┬─(有修改意见)─Send×N─▶ design
             │                                             └─(通过)─▶ 按依赖分批 Send ─▶ solve ─▶ solve_join ─┐
             └─(不确认：按需设计，solve 内先设计再求解)──────────────────────▲
                                                       ▲                                     │
                                                       └──────────── 还有未求解的子问题 ◀──────┘
    solve_join ──(全部求解)──▶ reconcile（结论一致性）─▶ write ─▶ validate ─┬─(有问题且未超轮数)─▶ repair ─▶ validate
                                                  └─▶ render ─▶ END
"""

from typing import List, Union

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send

from .context import GraphContext
from .nodes.analyze import analyze
from .nodes.confirm import confirm
from .nodes.consistency import reconcile
from .nodes.design import design
from .nodes.solve import ready_subproblems, solve
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


def send_solves(state: PaperState) -> Union[List[Send], str]:
    solutions = state.get("solutions") or {}
    if all(sp["id"] in solutions for sp in state["analysis"]["subproblems"]):
        return "reconcile"
    sends = []
    for sp in ready_subproblems(state):
        sends.append(Send("solve", {
            "subproblem": sp,
            "analysis": state["analysis"],
            "data_profile": state.get("data_profile", ""),
            "design": (state.get("designs") or {}).get(sp["id"]),
            "dependencies": {dep: solutions[dep] for dep in sp.get("depends_on") or [] if dep in solutions},
        }))
    return sends


def route_after_analyze(state: PaperState) -> Union[List[Send], str]:
    return send_designs(state) if state.get("plan_ahead") else send_solves(state)


def route_after_confirm(state: PaperState) -> Union[List[Send], str]:
    if not state.get("approved", True):
        return send_designs(state)
    return send_solves(state)


async def solve_join(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    """并行求解的汇合点，保证路由只算一次；按需设计时，所有子问题都设计过后标记设计阶段完成"""
    designs = state.get("designs") or {}
    if not state.get("plan_ahead") and all(sp["id"] in designs for sp in state["analysis"]["subproblems"]):
        await runtime.context.set_stage("design", "completed")
    return {}


def build_graph():
    graph = StateGraph(PaperState, context_schema=GraphContext)
    graph.add_node("analyze", analyze)
    graph.add_node("design", design)
    graph.add_node("confirm", confirm)
    graph.add_node("solve", solve)
    graph.add_node("solve_join", solve_join)
    graph.add_node("reconcile", reconcile)
    graph.add_node("write", write)
    graph.add_node("validate", validate)
    graph.add_node("repair", repair)
    graph.add_node("render", render)

    graph.add_edge(START, "analyze")
    graph.add_conditional_edges("analyze", route_after_analyze, ["design", "solve", "reconcile"])
    graph.add_edge("design", "confirm")
    graph.add_conditional_edges("confirm", route_after_confirm, ["design", "solve", "reconcile"])
    graph.add_edge("solve", "solve_join")
    graph.add_conditional_edges("solve_join", send_solves, ["solve", "reconcile"])
    graph.add_edge("reconcile", "write")
    graph.add_edge("write", "validate")
    graph.add_conditional_edges("validate", route_after_validate, ["repair", "render"])
    graph.add_edge("repair", "validate")
    graph.add_edge("render", END)
    return graph


def compile_graph(checkpointer=None):
    return build_graph().compile(checkpointer=checkpointer)
