"""Writing consumes execution records and result files and produces sections and publications."""
from types import SimpleNamespace
from importlib import import_module
from ..registry import CapabilityProduct, Inputs


def register(registry):
    for name, owner in (("write", "sections"),
                        ("validate", "validation"), ("repair", "validation"),
                        ("render", "validation"), ("export", "export")):
        async def run(ctx, inputs, name=name, owner=owner):
            from ai_system.research.control import graph_view
            state = graph_view(ctx.research.snapshot())
            if ctx.output_mode == 'latex' and name in {'write','repair'}:
                owner = 'latex'
            handler = getattr(import_module('ai_system.modules.writing.' + owner), name)
            value = await handler(state, SimpleNamespace(context=ctx))
            return CapabilityProduct("paper", value)
        registry.register("writing", f"writing.{name}", "paper", run, Inputs)
