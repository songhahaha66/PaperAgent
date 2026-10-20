"""Writing consumes current evidence and produces sections and publications."""
from types import SimpleNamespace
from importlib import import_module
from ..registry import CapabilityProduct, StateInputs


def register(registry):
    for name, owner in (("reconcile", "consistency"), ("write", "sections"),
                        ("validate", "validation"), ("repair", "validation"),
                        ("render", "validation"), ("export", "export")):
        async def run(ctx, inputs, name=name, owner=owner):
            state = inputs['state']
            if ctx.output_mode == 'latex' and ctx.research and name in {'write','repair'}:
                owner = 'latex'
            handler = getattr(import_module('ai_system.modules.writing.' + owner), name)
            value = await handler(state, SimpleNamespace(context=ctx))
            return CapabilityProduct("paper", value)
        registry.register("writing", f"writing.{name}", "paper", run, StateInputs)
