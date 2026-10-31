"""Shared research contexts and model requests for offline tests."""
from ai_system.graph.context import GraphContext
from ai_system.research.store import ResearchStore


def _context(root, model, research_id, requirement):
    ctx = GraphContext.create(str(root), planner_llm=model)
    ctx.research = ResearchStore(root, research_id)
    ctx.research.initialize(requirement)
    return ctx


def session_context(root, model=None):
    return _context(root, model, 'sessions', '效率0.95；使用附件计算并保存真实结果。')


def delivery_context(root, model=None):
    return _context(root, model, 'context-delivery', '效率0.95，保存结果与简报。')


class ContextRequest:
    tools = []

    def __init__(self, messages, system=None):
        self.messages = messages
        self.system_message = system

    def override(self, **kwargs):
        return ContextRequest(kwargs['messages'], self.system_message)
