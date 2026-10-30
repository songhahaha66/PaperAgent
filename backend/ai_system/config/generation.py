"""Connection-owned generation settings; auxiliary calls have one small policy."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TokenPrices(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    input_per_million:float=Field(ge=0)
    output_per_million:float=Field(ge=0)
    cached_input_per_million:float|None=Field(default=None,ge=0)


class GenerationSettings(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    max_output_tokens:int=Field(default=16000,ge=256,le=1000000)
    context_window:int|None=Field(default=None,ge=1024,le=10000000)
    reasoning_effort:Literal['minimal','low','medium','high','xhigh','max']|None=None
    prices:TokenPrices|None=None

    @model_validator(mode='after')
    def room_for_input(self):
        if self.context_window is not None and self.context_window<=self.max_output_tokens:
            raise ValueError('上下文窗口必须大于生成上限，为输入保留空间')
        return self


def request_model(llm, purpose='research'):
    """Research/structured/code use the configured generation ceiling, never a thinking heuristic."""
    if purpose not in {'summary','auxiliary'}:return llm
    fields=getattr(type(llm),'model_fields',{})
    if 'max_tokens' not in fields:return llm
    changes={'max_tokens':min(getattr(llm,'max_tokens',None) or 16000,1800 if purpose=='summary' else 2500)}
    body=getattr(llm,'extra_body',None) or {}
    if 'thinking' in body:
        changes['extra_body']={**body,'thinking':{'type':'disabled'}}
    if getattr(llm,'reasoning_effort',None) is not None:changes['reasoning_effort']='low'
    return llm.model_copy(update=changes)


class ModelGenerationLimit(RuntimeError):
    """A complete response was not produced; no partial tool operation may run."""


class ModelContextLimit(ModelGenerationLimit):pass

def completion_info(reply):
    messages=getattr(reply,'result',None) or getattr(reply,'generations',None) or [reply]
    for item in messages:
        message=getattr(item,'message',item)
        meta={**(getattr(item,'generation_info',None) or {}),**(getattr(message,'response_metadata',None) or {})}
        reason=meta.get('finish_reason') or meta.get('stop_reason')
        details=meta.get('incomplete_details') or {}
        if reason in {'length','max_tokens','model_context_window_exceeded'} or meta.get('status')=='incomplete':
            return {'finish_reason':reason or details.get('reason','incomplete'),
                    'kind':'context_limit' if reason=='model_context_window_exceeded' else 'generation_limit'}
    return {}


def ensure_complete(reply):
    info=completion_info(reply)
    if info.get('kind')=='context_limit':raise ModelContextLimit('模型上下文容量不足，未执行残缺工具调用；请缩小输入或压缩历史后恢复。')
    if info:raise ModelGenerationLimit('模型生成预算耗尽，响应被截断；未执行残缺工具调用。请调整推理强度或生成上限后恢复，已有结果保留。')
