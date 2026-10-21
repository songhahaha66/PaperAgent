"""The only dispatch boundary for research capabilities.

Modules own methods and products. LangGraph owns scheduling/checkpointing only.
Registration fails on collisions and unknown capabilities fail closed.
"""
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal
import inspect

from pydantic import BaseModel, ConfigDict, Field
from ai_system.config.observability import capability_span


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
    purpose: str = ''
    model_callable: bool = False


class ModuleRegistry:
    def __init__(self):
        self._capabilities = {}

    def register(self, module, name, product, handler, inputs=Inputs, *, purpose='', model_callable=False):
        if name in self._capabilities:
            raise ValueError(f"重复能力：{name}")
        if model_callable and not purpose.strip():raise ValueError('模型可调用能力必须声明用途和产物边界')
        self._capabilities[name] = Capability(name, module, product, handler, inputs,purpose,model_callable)

    def model_capabilities(self):
        return [c for c in self._capabilities.values() if c.model_callable]

    def research_tools(self):
        return [{'name':c.name,'purpose':c.purpose,'inputs':c.inputs.model_json_schema(),'product':c.product}
                for c in self._capabilities.values() if c.model_callable]

    def validate_research_tool(self,name,inputs):
        capability=self._capabilities.get(name)
        if not capability or not capability.model_callable:raise ValueError('该能力未显式开放给研究控制器')
        return capability.inputs.model_validate(inputs)

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
            from ai_system.research.views import activity
            branch=request.inputs.get('branch') or {}
            task_id=request.inputs.get('task_id') or request.inputs.get('experiment_id') or branch.get('subproblem',{}).get('id')
            with activity(ctx,request.name,task_id,**({'question_id':request.inputs['question_id']} if request.inputs.get('question_id') else {})) as execution:
                execution['inputs'] = request.inputs if not request.name.startswith('writing.') else {'source':'当前研究与版本化正文'}
                with capability_span(request.name, capability.module, request.inputs) as span:
                    result = capability.execute(ctx, capability.inputs.model_validate(request.inputs).model_dump())
                    if inspect.isawaitable(result):
                        result = await result
                    if not isinstance(result, CapabilityProduct) or result.kind != capability.product:
                        raise TypeError(f"能力产物不符合接口：{request.name}")
                    if isinstance(result.value,dict) and not request.name.startswith('writing.'):
                        execution['output'] = result.value
                        if result.value.get('question_id'):execution['question_id']=result.value['question_id']
                        if result.value.get('experiment_id'):execution['experiment_id']=result.value['experiment_id']
                    if isinstance(result.value,dict) and result.value.get('research_blocked'):
                        execution.update(status='failed',message=result.value.get('message'))
                    if span is not None:
                        span.end(outputs={'kind': result.kind, 'value': result.value})
        finally:
            CURRENT_CAPABILITY.reset(token)
        return result

    def invoke_sync(self, request: CapabilityRequest, ctx):
        capability = self._capabilities.get(request.name)
        if capability is None:
            raise ValueError(f"未注册能力：{request.name}")
        if inspect.iscoroutinefunction(capability.execute):
            raise TypeError(f"异步能力不能同步调用：{request.name}")
        with capability_span(request.name, capability.module, request.inputs) as span:
            result = capability.execute(ctx, capability.inputs.model_validate(request.inputs).model_dump())
            if not isinstance(result, CapabilityProduct) or result.kind != capability.product:
                raise TypeError(f"能力产物不符合接口：{request.name}")
            if span is not None:
                span.end(outputs={'kind': result.kind, 'value': result.value})
        return result


_registry = None


def capabilities():
    global _registry
    if _registry is None:
        from . import literature, writing, experience
        from ai_system.research import feedback, operations
        from .experiments import module
        registry = ModuleRegistry()
        for owner in (literature, module, writing, experience, feedback, operations):
            owner.register(registry)
        _registry = registry
    return _registry


async def invoke(ctx, name, **inputs):
    return (await capabilities().invoke(CapabilityRequest(name=name, inputs=inputs), ctx)).value


def invoke_sync(ctx, name, **inputs):
    return capabilities().invoke_sync(CapabilityRequest(name=name, inputs=inputs), ctx).value
