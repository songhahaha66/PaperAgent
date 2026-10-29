"""Experience produces candidates; validation and adoption are explicit actions."""
from .registry import CapabilityProduct, ExperienceInputs


def index(ctx, inputs):
    count = ctx.experience_scope.index(ctx.research) if ctx.experience_scope else 0
    return CapabilityProduct("experience", {"candidates": count})


def search(ctx, inputs):
    value = ctx.experience_scope.search(**inputs) if ctx.experience_scope else []
    return CapabilityProduct("experience", value)


def register(registry):
    registry.register("experience", "experience.candidates", "experience", index)
    registry.register("experience", "experience.search", "experience", search, ExperienceInputs)
