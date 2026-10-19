"""Experiment capabilities own planning, exploration, comparison and execution."""
from types import SimpleNamespace
from pydantic import Field
from ai_system.modules.registry import Inputs

class CommentInputs(Inputs):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    code: str = Field(min_length=1,max_length=12000)
    inputs: list[str] = Field(default_factory=list,max_length=100)

async def comment(ctx, inputs):
    from services.chat_services.paper_comments import calculate
    return await calculate(ctx, inputs)

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
    registry.register("experiments", "experiments.comment", "experiment", comment, CommentInputs)
    for name, product, handler in (("plan", "research_state", plan), ("explore", "experiment", explore), ("compare", "experiment", compare),
                                    ("design", "experiment", design), ("execute", "research_state", execute),
                                    ("analyze", "research_state", analyze)):
        registry.register("experiments", f"experiments.{name}", product, handler, StateInputs if name=='analyze' else BranchInputs)
