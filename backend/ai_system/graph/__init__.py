"""LangGraph execution of observe → act → solve_join → observe."""
from .build import build_graph, compile_graph
from .context import GraphContext
from .registry import ResultRegistry


def __getattr__(name):
    if name == 'PaperGraphAgent':
        from .agent import PaperGraphAgent
        return PaperGraphAgent
    raise AttributeError(name)
