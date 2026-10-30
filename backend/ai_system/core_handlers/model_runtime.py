"""One bounded retry owner for model requests; durable accounting lives in research.runtime.

Only model requests are retried here. Tools and graph nodes are never replayed.
A streamed response is not replayed after any public/tool/reasoning content is emitted.
"""
import asyncio
import time
import httpx
from pydantic import model_validator
from urllib.parse import urlparse
from ..research.runtime import ACTIVE_BUDGET, ACTIVE_MODEL_REQUEST, ModelServiceError,ResearchBudgetExhausted
from ..config.generation import ModelGenerationLimit,ModelContextOverflow,completion_info,ensure_complete
from ..research.store import digest

RETRY_DELAYS=(1.0,2.0)


def model_identity(model):
    fields={key:str(getattr(model,key,None)) for key in ('model_name','model','openai_api_base','anthropic_api_url','request_timeout','default_request_timeout','timeout','extra_body','reasoning_effort')}
    # Credentials only enter a one-way local fingerprint; never persist their values.
    for key in ('openai_api_key','anthropic_api_key','google_api_key'):
        value=getattr(model,key,None)
        fields[key]=digest(value.get_secret_value() if hasattr(value,'get_secret_value') else str(value))
    return digest(fields)


def operation_key(runtime,model,args,kwargs):
    from ..graph.tracing import CURRENT_LABEL
    return digest({'model':model_identity(model),'label':CURRENT_LABEL.get(),'args':str(args),
                   'options':{k:str(v) for k,v in kwargs.items() if k not in {'run_manager','callbacks'}},
                   'output_limit':getattr(model,'max_tokens',None),'inputs':runtime.store.file_inputs(),'policy':runtime.policy()})


def failure(exc):
    status=getattr(exc,'status_code',None) or getattr(getattr(exc,'response',None),'status_code',None)
    if status is None and isinstance(getattr(exc,'code',None),int):status=exc.code
    body=getattr(exc,'body',None) or {}
    if isinstance(body,dict) and isinstance(body.get('error'),dict):body=body['error']
    code=str(body.get('code') or body.get('type') or '') if isinstance(body,dict) else ''
    quota=code.lower() in {'insufficient_quota','insufficient_balance','balance_not_enough','billing_hard_limit_reached','credit_balance_too_low'}
    if status in {401,403} or type(exc).__name__ in {'AuthenticationError','PermissionDeniedError'}:
        return 'authentication','模型连接认证或权限失败，请检查连接后恢复。',False,True
    if status==402 or quota:
        return 'quota','模型服务余额或额度不足，请处理后恢复。',False,True
    # Match explicit provider error codes (or Anthropic's documented 400 form),
    # never a generic length/truncation error, auth failure or network timeout.
    if status in {400,413} and (code.lower() in {'context_length_exceeded','max_context_length_exceeded','context_window_exceeded'}
            or isinstance(body,dict) and str(body.get('message','')).lower().startswith('prompt is too long:')):
        return 'context_limit','模型拒绝超出上下文容量的请求；可压缩后重发一次。',False,False
    if isinstance(exc, httpx.NetworkError) or type(exc).__name__ == 'APIConnectionError':
        return 'connection','连接模型服务失败，请检查网络；已有进度已保存。',True,False
    transient=status in {408,429} or isinstance(status,int) and status>=500 or isinstance(exc,(TimeoutError,ConnectionError,httpx.TransportError)) or type(exc).__name__ in {'APITimeoutError','APIConnectionError','ReadTimeout','ConnectTimeout','RateLimitError'}
    return ('rate_limit' if status==429 else 'timeout' if isinstance(exc,TimeoutError) or 'Timeout' in type(exc).__name__ else 'service_error',
            '模型服务暂时不可用，已保存状态；有限重试后可检查连接和输入。' if transient else '模型请求失败，已保留状态；请检查连接或请求内容。',bool(transient),False)


def failure_diagnostic(exc):
    """Retain transport causes without credentials, request bodies or response text."""
    chain=[];seen=set();current=exc
    while current is not None and id(current) not in seen and len(chain)<8:
        seen.add(id(current));chain.append(type(current).__name__)
        current=current.__cause__ or current.__context__
    detail={'error_type':type(exc).__name__,'error_chain':chain,'error_kind':failure(exc)[0]}
    status=getattr(exc,'status_code',None)
    if isinstance(status,int):detail['http_status']=status
    return detail


def reply_usage(reply):
    direct=getattr(reply,'usage_metadata',None)
    if direct:return direct
    for generation in getattr(reply,'generations',[]) or []:
        usage=getattr(getattr(generation,'message',None),'usage_metadata',None)
        if usage:return usage
    return None


def reservation(model,args,kwargs):
    from ..graph.compaction import token_estimate
    from langchain_core.messages import messages_to_dict
    messages=args[0] if args else kwargs.get('messages',[])
    try:body=messages_to_dict(messages)
    except (TypeError,AttributeError):body=str(messages)
    estimated=token_estimate([body,kwargs.get('tools',[]),kwargs.get('functions',[])])
    profile=getattr(model,'profile',None) or {}
    ceiling=profile.get('max_context_tokens')
    if ceiling and estimated+int(getattr(model,'max_tokens',None) or 16000)>ceiling:
        raise ModelContextOverflow('请求输入与生成预留空间超过模型上下文容量；未发送请求，请先压缩历史或缩小输入。')
    return {'input_tokens':estimated,
            'output_tokens':int(getattr(model,'max_tokens',None) or 16000),
            'prices':((getattr(model,'metadata',None) or {}).get('generation_settings') or {}).get('prices')}


def finish(runtime,seq,reply,usage=None):
    info=completion_info(reply)
    runtime.usage(seq,reply_usage(reply) if usage is None else usage,
                  state='truncated' if info else 'completed',diagnostic=info or None)
    if info:
        runtime.note_stop(info['kind'],'模型生成预算耗尽，未执行残缺工具调用；已有结果保留。')
        ensure_complete(reply)




class RuntimeModelMixin:
    @model_validator(mode='before')
    @classmethod
    def one_retry_owner(cls, values):
        # Set before SDK clients are constructed; copying a LangChain field later
        # would leave the already-created SDK client's retry count unchanged.
        return {**values,'max_retries':0} if isinstance(values,dict) else values

    def _scope(self,args,kwargs):
        runtime=ACTIVE_BUDGET.get()
        if runtime is None or ACTIVE_MODEL_REQUEST.get() is not None:return None
        return runtime,operation_key(runtime,self,args,kwargs),'connection:'+model_identity(self)

    async def _agenerate(self,*args,**kwargs):
        scope=self._scope(args,kwargs)
        if scope is None:
            result=await super()._agenerate(*args,**kwargs)
            if ACTIVE_MODEL_REQUEST.get() is None:ensure_complete(result)
            return result
        if getattr(self,'max_retries',0):return await self.model_copy(update={'max_retries':0})._agenerate(*args,**kwargs)
        runtime,key,connection=scope
        for attempt in range(3):
            runtime.check_guard(connection);runtime.check_guard(key)
            seq=runtime.reserve(urlparse(str(getattr(self,'openai_api_base','') or '')).hostname or type(self).__name__,**reservation(self,args,kwargs))
            token=ACTIVE_MODEL_REQUEST.set((runtime,seq))
            try:
                # Legacy/explicit per-project timeouts remain overrides. New projects
                # use the model client's configured request timeout, not a total 90s timer.
                async with asyncio.timeout(runtime.policy().get('timeout_seconds')):
                    bounded=self.model_copy(update={'max_tokens':runtime.generation_limit(seq)})
                    result=await super(RuntimeModelMixin,bounded)._agenerate(*args,**kwargs)
                finish(runtime,seq,result);runtime.succeeded(key)
                return result
            except (ModelGenerationLimit,ResearchBudgetExhausted):raise
            except (asyncio.CancelledError, GeneratorExit):
                runtime.usage(seq,state="interrupted");raise
            except Exception as exc:
                runtime.usage(seq,state="failed",diagnostic=failure_diagnostic(exc))
                kind,message,retry,terminal=failure(exc)
                if kind=='context_limit':raise ModelContextOverflow(message) from exc
                runtime.failed(connection if terminal else key,digest([type(exc).__name__,kind,getattr(exc,'status_code',None)]),kind,message,terminal=terminal)
                if not retry or attempt==2:raise ModelServiceError(message) from exc
            finally:ACTIVE_MODEL_REQUEST.reset(token)
            await asyncio.sleep(RETRY_DELAYS[attempt])

    async def _astream(self,*args,**kwargs):
        scope=self._scope(args,kwargs)
        if scope is None:
            async for chunk in super()._astream(*args,**kwargs):yield chunk
            return
        if getattr(self,'max_retries',0):
            async for chunk in self.model_copy(update={'max_retries':0})._astream(*args,**kwargs):yield chunk
            return
        runtime,key,connection=scope
        for attempt in range(3):
            runtime.check_guard(connection);runtime.check_guard(key)
            seq=runtime.reserve(type(self).__name__,**reservation(self,args,kwargs));token=ACTIVE_MODEL_REQUEST.set((runtime,seq));emitted=False;usage={};last=None;truncated=None
            try:
                async with asyncio.timeout(runtime.policy().get('timeout_seconds')):
                    bounded=self.model_copy(update={'max_tokens':runtime.generation_limit(seq)})
                    async for chunk in super(RuntimeModelMixin,bounded)._astream(*args,**kwargs):
                        message=getattr(chunk,'message',chunk);last=chunk
                        if completion_info(chunk):truncated=chunk
                        reported=reply_usage(message)
                        if reported:
                            for k in ('input_tokens','output_tokens'):usage[k]=max(usage.get(k,0),int(reported.get(k) or 0))
                            for detail in ('input_token_details','output_token_details'):
                                if detail in reported:usage[detail]=reported[detail]
                        emitted=emitted or bool(getattr(message,'content',None) or getattr(message,'tool_call_chunks',None) or getattr(message,'tool_calls',None) or (getattr(message,'additional_kwargs',{}) or {}).get('reasoning_content'))
                        yield chunk
                finish(runtime,seq,truncated or last,usage);runtime.succeeded(key);return
            except (ModelGenerationLimit,ResearchBudgetExhausted):raise
            except (asyncio.CancelledError, GeneratorExit):
                runtime.usage(seq,usage,state="interrupted");raise
            except Exception as exc:
                runtime.usage(seq,usage,state="failed",diagnostic=failure_diagnostic(exc));kind,message,retry,terminal=failure(exc)
                if kind=='context_limit' and not emitted:raise ModelContextOverflow(message) from exc
                runtime.failed(connection if terminal else key,digest([type(exc).__name__,kind,getattr(exc,'status_code',None)]),kind,message,terminal=terminal)
                if emitted or not retry or attempt==2:raise ModelServiceError(message) from exc
            finally:ACTIVE_MODEL_REQUEST.reset(token)
            await asyncio.sleep(RETRY_DELAYS[attempt])

    def _generate(self,*args,**kwargs):
        scope=self._scope(args,kwargs)
        if scope is None:
            result=super()._generate(*args,**kwargs)
            if ACTIVE_MODEL_REQUEST.get() is None:ensure_complete(result)
            return result
        if getattr(self,'max_retries',0):return self.model_copy(update={'max_retries':0})._generate(*args,**kwargs)
        runtime,key,connection=scope
        for attempt in range(3):
            runtime.check_guard(connection);runtime.check_guard(key)
            seq=runtime.reserve(type(self).__name__,**reservation(self,args,kwargs));token=ACTIVE_MODEL_REQUEST.set((runtime,seq))
            try:
                bounded=self.model_copy(update={'max_tokens':runtime.generation_limit(seq)})
                result=super(RuntimeModelMixin,bounded)._generate(*args,**kwargs)
                finish(runtime,seq,result);runtime.succeeded(key);return result
            except (ModelGenerationLimit,ResearchBudgetExhausted):raise
            except Exception as exc:
                runtime.usage(seq,state="failed",diagnostic=failure_diagnostic(exc));kind,message,retry,terminal=failure(exc)
                if kind=='context_limit':raise ModelContextOverflow(message) from exc
                runtime.failed(connection if terminal else key,digest([type(exc).__name__,kind,getattr(exc,'status_code',None)]),kind,message,terminal=terminal)
                if not retry or attempt==2:raise ModelServiceError(message) from exc
            finally:ACTIVE_MODEL_REQUEST.reset(token)
            time.sleep(RETRY_DELAYS[attempt])

    def _stream(self,*args,**kwargs):
        scope=self._scope(args,kwargs)
        if scope is None:
            yield from super()._stream(*args,**kwargs);return
        if getattr(self,'max_retries',0):
            yield from self.model_copy(update={'max_retries':0})._stream(*args,**kwargs);return
        runtime,key,connection=scope
        for attempt in range(3):
            runtime.check_guard(connection);runtime.check_guard(key)
            seq=runtime.reserve(type(self).__name__,**reservation(self,args,kwargs));token=ACTIVE_MODEL_REQUEST.set((runtime,seq));emitted=False;usage={};last=None;truncated=None
            try:
                bounded=self.model_copy(update={'max_tokens':runtime.generation_limit(seq)})
                for chunk in super(RuntimeModelMixin,bounded)._stream(*args,**kwargs):
                    message=getattr(chunk,'message',chunk);last=chunk;reported=reply_usage(message)
                    if completion_info(chunk):truncated=chunk
                    if reported:
                        for k in ('input_tokens','output_tokens'):usage[k]=max(usage.get(k,0),int(reported.get(k) or 0))
                        for detail in ('input_token_details','output_token_details'):
                            if detail in reported:usage[detail]=reported[detail]
                    emitted=emitted or bool(getattr(message,'content',None) or getattr(message,'tool_call_chunks',None) or getattr(message,'tool_calls',None) or (getattr(message,'additional_kwargs',{}) or {}).get('reasoning_content'))
                    yield chunk
                finish(runtime,seq,truncated or last,usage);runtime.succeeded(key);return
            except (ModelGenerationLimit,ResearchBudgetExhausted):raise
            except GeneratorExit:
                runtime.usage(seq,usage,state="interrupted");raise
            except Exception as exc:
                runtime.usage(seq,usage,state="failed",diagnostic=failure_diagnostic(exc));kind,message,retry,terminal=failure(exc)
                if kind=='context_limit' and not emitted:raise ModelContextOverflow(message) from exc
                runtime.failed(connection if terminal else key,digest([type(exc).__name__,kind,getattr(exc,'status_code',None)]),kind,message,terminal=terminal)
                if emitted or not retry or attempt==2:raise ModelServiceError(message) from exc
            finally:ACTIVE_MODEL_REQUEST.reset(token)
            time.sleep(RETRY_DELAYS[attempt])
