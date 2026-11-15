"""Research context and presentation helpers."""
from .context import GraphContext
from .registry import ResultRegistry


def __getattr__(name):
    if name=='PaperAgent':
        from ai_system.agent.agent import PaperAgent
        return PaperAgent
    raise AttributeError(name)
