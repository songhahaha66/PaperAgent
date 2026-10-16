"""
状态图版本的数模论文流水线（聊天接口和评测都用它）

analyze/confirm → observe → solve/commit → observe → write → validate/repair → render
"""

from .agent import PaperGraphAgent
from .build import build_graph, compile_graph
from .context import GraphContext
from .registry import GroundingError, ResultRegistry

__all__ = ["PaperGraphAgent", "build_graph", "compile_graph", "GraphContext", "GroundingError", "ResultRegistry"]
