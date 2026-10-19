"""Writing consumes current evidence and produces sections and publications."""
from types import SimpleNamespace
from importlib import import_module
from ..registry import CapabilityProduct, StateInputs


def register(registry):
    for name, owner in (("reconcile", "consistency"), ("write", "sections"),
                        ("validate", "validation"), ("repair", "validation"),
                        ("render", "validation"), ("export", "export")):
        async def run(ctx, inputs, name=name, owner=owner):
            handler = getattr(import_module('ai_system.modules.writing.' + owner), name)
            return CapabilityProduct("paper", await handler(inputs["state"], SimpleNamespace(context=ctx)))
        registry.register("writing", f"writing.{name}", "paper", run, StateInputs)
