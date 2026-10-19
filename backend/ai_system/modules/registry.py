"""The only dispatch boundary for research capabilities.

Modules own methods and products. LangGraph owns scheduling/checkpointing only.
Registration fails on collisions and unknown capabilities fail closed.
"""
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal
import inspect

from pydantic import BaseModel, ConfigDict, Field


class CapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    inputs: dict = Field(default_factory=dict)


class Inputs(BaseModel):
    model_config = ConfigDict(extra='forbid')


class StateInputs(Inputs):
    state: dict = Field(min_length=1)


class BranchInputs(Inputs):
    branch: dict = Field(min_length=1)


class LiteratureInputs(Inputs):
    question: str = Field(min_length=1)
    title: str = ''


class SourceInputs(Inputs):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=3, ge=1, le=10)
    fetch_open_access: bool = True


class CitationInputs(Inputs):
    texts: list[str]


class ExperienceInputs(Inputs):
    question: str
    methods: list[str]
    actual: dict


@dataclass(frozen=True)
class CapabilityProduct:
    kind: Literal["research_state", "literature", "experiment", "experience", "paper"]
    value: Any


@dataclass(frozen=True)
class Capability:
    name: str
    module: str
    product: str
    execute: Callable[..., Awaitable[CapabilityProduct]]
    inputs: type[BaseModel]


class ModuleRegistry:
    def __init__(self):
        self._capabilities = {}

    def register(self, module, name, product, handler, inputs=Inputs):
        if name in self._capabilities:
            raise ValueError(f"重复能力：{name}")
        self._capabilities[name] = Capability(name, module, product, handler, inputs)

    def describe(self):
        return [{"name": c.name, "module": c.module, "product": c.product, "inputs": c.inputs.model_json_schema()}
                for c in self._capabilities.values()]

    async def invoke(self, request: CapabilityRequest, ctx):
        capability = self._capabilities.get(request.name)
        if capability is None:
            raise ValueError(f"未注册能力：{request.name}")
        from ai_system.graph.tracing import CURRENT_CAPABILITY
        token = CURRENT_CAPABILITY.set(request.name)
        try:
            result = capability.execute(ctx, capability.inputs.model_validate(request.inputs).model_dump())
            if inspect.isawaitable(result):
                result = await result
        finally:
            CURRENT_CAPABILITY.reset(token)
        if not isinstance(result, CapabilityProduct) or result.kind != capability.product:
            raise TypeError(f"能力产物不符合接口：{request.name}")
        return result

    def invoke_sync(self, request: CapabilityRequest, ctx):
        capability = self._capabilities.get(request.name)
        if capability is None:
            raise ValueError(f"未注册能力：{request.name}")
        if inspect.iscoroutinefunction(capability.execute):
            raise TypeError(f"异步能力不能同步调用：{request.name}")
        result = capability.execute(ctx, capability.inputs.model_validate(request.inputs).model_dump())
        if not isinstance(result, CapabilityProduct) or result.kind != capability.product:
            raise TypeError(f"能力产物不符合接口：{request.name}")
        return result


_registry = None


def capabilities():
    global _registry
    if _registry is None:
        from . import literature, writing, experience
        from .experiments import module
        registry = ModuleRegistry()
        for owner in (literature, module, writing, experience):
            owner.register(registry)
        _registry = registry
    return _registry


async def invoke(ctx, name, **inputs):
    return (await capabilities().invoke(CapabilityRequest(name=name, inputs=inputs), ctx)).value


def invoke_sync(ctx, name, **inputs):
    return capabilities().invoke_sync(CapabilityRequest(name=name, inputs=inputs), ctx).value
