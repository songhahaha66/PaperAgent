"""
状态图版本的数模论文流水线（AI_PIPELINE=v2）

analyze → design(候选 + 评审) → confirm → solve(按依赖并行) → write → validate/repair → render
"""

from .agent import PaperGraphAgent
from .build import build_graph, compile_graph
from .context import GraphContext
from .registry import GroundingError, ResultRegistry

__all__ = ["PaperGraphAgent", "build_graph", "compile_graph", "GraphContext", "GroundingError", "ResultRegistry"]
