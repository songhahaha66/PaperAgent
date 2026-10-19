"""Experiment capabilities own planning, exploration, comparison and execution."""
from types import SimpleNamespace
from ai_system.modules.registry import CapabilityProduct, StateInputs, BranchInputs


async def explore(ctx, inputs):
    from .exploration import explore
    return CapabilityProduct("experiment", await explore(ctx, inputs["branch"]))


async def compare(ctx, inputs):
    from .supervised import compare
    return CapabilityProduct("experiment", await compare(ctx, inputs["branch"]))


async def design(ctx, inputs):
    from ai_system.modules.experiments.planning import make_design
    return CapabilityProduct("experiment", await make_design(ctx, inputs["branch"]))


async def execute(ctx, inputs):
    from ai_system.research.control import execute
    return CapabilityProduct("research_state", await execute(inputs["branch"], SimpleNamespace(context=ctx)))


async def analyze(ctx, inputs):
    from ai_system.modules.experiments.analysis import analyze
    return CapabilityProduct("research_state", await analyze(inputs["state"], SimpleNamespace(context=ctx)))


async def plan(ctx, inputs):
    from .planning import design
    return CapabilityProduct('research_state', await design(inputs['branch'], SimpleNamespace(context=ctx)))


def register(registry):
    for name, product, handler in (("plan", "research_state", plan), ("explore", "experiment", explore), ("compare", "experiment", compare),
                                    ("design", "experiment", design), ("execute", "research_state", execute),
                                    ("analyze", "research_state", analyze)):
        registry.register("experiments", f"experiments.{name}", product, handler, StateInputs if name=='analyze' else BranchInputs)
