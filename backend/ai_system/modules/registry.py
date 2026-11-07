"""The catalogue and validated handler boundary for research capabilities.

ToolRuntime owns dispatch, receipts and Item lifecycle. Modules own products.
LangGraph owns scheduling/checkpointing only.
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
    execution_status: Literal['completed', 'failed', 'interrupted'] | None = None
    execution_message: str | None = None


@dataclass(frozen=True)
class Capability:
    name: str
    module: str
    product: str
    execute: Callable[..., Awaitable[CapabilityProduct]]
    inputs: type[BaseModel]
    purpose: str = ''
    model_callable: bool = False
    access: Literal['read','write'] = 'write'


def display_inputs(name, inputs):
    """Keep writing metadata without copying manuscripts or replacement text."""
    if not name.startswith('writing.'):
        return inputs
    if name == 'writing.edit':
        return {'path': inputs.get('path', 'paper.md'),
                'edit_mode': 'replace' if inputs.get('old_text') is not None else 'write'}
    if name == 'writing.preview':
        pages = inputs.get('pages', [1])
        return {'pages': [page for page in pages[:4] if type(page) is int] if isinstance(pages, list) else [],
                'historical': bool(inputs.get('source_version'))}
    if name == 'writing.use_asset':
        return {key: inputs[key] for key in ('source', 'destination') if key in inputs}
    return {}


def display_output(name, value):
    if not name.startswith('writing.'):
        return value
    if name == 'writing.preview':
        return {**{key: value[key] for key in ('total_pages', 'compiled', 'current') if key in value},
                'pages': [page['page'] for page in value.get('pages', []) if isinstance(page, dict) and type(page.get('page')) is int],
                'warnings': [warning[:400] for warning in value.get('warnings', [])[:20] if isinstance(warning, str)],
                'warning_count': len(value.get('warnings', []))}
    keys = {'writing.edit': ('path',), 'writing.use_asset': ('path',),
            'writing.propose_edits': ('status',), 'writing.export': ('exports',)}.get(name, ())
    return {key: value[key] for key in keys if key in value}


class ModuleRegistry:
    def __init__(self):
        self._capabilities = {}

    def register(self, module, name, product, handler, inputs=Inputs, *, purpose='', model_callable=False, access='write'):
        if name in self._capabilities:
            raise ValueError(f"重复能力：{name}")
        if model_callable and not purpose.strip():raise ValueError('模型可调用能力必须声明用途和产物边界')
        if access not in {'read','write'}:raise ValueError('Invalid capability access')
        self._capabilities[name] = Capability(name, module, product, handler, inputs,purpose,model_callable,access)

    def model_capabilities(self):
        return [c for c in self._capabilities.values() if c.model_callable]


    def describe(self):
        return [{"name": c.name, "module": c.module, "product": c.product, "inputs": c.inputs.model_json_schema()}
                for c in self._capabilities.values()]

    def get(self, name):
        return self._capabilities.get(name)

    async def invoke(self, request: CapabilityRequest, ctx):
        from ai_system.agent.runtime import invoke
        result = await invoke(ctx, self, request.name, request.inputs)
        return CapabilityProduct(result.kind, result.value, result.status, result.message)

    async def execute(self, name, inputs, ctx):
        """Handler/schema boundary. ToolRuntime owns identity and lifecycle."""
        capability = self.get(name)
        if capability is None:
            raise ValueError(f"未注册能力：{name}")
        from ai_system.graph.tracing import CURRENT_CAPABILITY
        token = CURRENT_CAPABILITY.set(name)
        try:
            with capability_span(name, capability.module, inputs) as span:
                result = capability.execute(ctx, capability.inputs.model_validate(inputs).model_dump())
                if inspect.isawaitable(result):result = await result
                if not isinstance(result, CapabilityProduct) or result.kind != capability.product:
                    raise TypeError(f"能力产物不符合接口：{name}")
                if span is not None:span.end(outputs={'kind':result.kind,'value':result.value})
                return result
        finally:
            CURRENT_CAPABILITY.reset(token)

    def execute_internal_sync(self, request: CapabilityRequest, ctx):
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
        from . import literature, writing, experience, delegation, skill_tools
        from ai_system.research import feedback, operations
        from .experiments import module
        registry = ModuleRegistry()
        for owner in (literature, module, writing, experience, feedback, operations, delegation, skill_tools):
            owner.register(registry)
        _registry = registry
    return _registry


async def invoke(ctx, name, **inputs):
    return (await capabilities().invoke(CapabilityRequest(name=name, inputs=inputs), ctx)).value


def execute_internal_sync(ctx, name, **inputs):
    return capabilities().execute_internal_sync(CapabilityRequest(name=name, inputs=inputs), ctx).value
