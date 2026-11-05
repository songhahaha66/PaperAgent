"""Exercise provider wrappers and the bounded runtime through controlled HTTP."""
import asyncio

import httpx2
import pytest

from ai_system.core_handlers import model_runtime
from ai_system.core_handlers.llm_providers import (
    MeteredChatAnthropic, MeteredChatGoogle, MeteredChatOpenAI,
)
from ai_system.research.runtime import ModelServiceError, ResearchRuntime, budget_scope


def model_with_transport(provider, sync, asynchronous, monkeypatch):
    options = dict(model='controlled', api_key='fixture-not-a-key', max_retries=6,
                   timeout=600, max_tokens=100)
    if provider == 'openai':
        model = MeteredChatOpenAI(**options, base_url='https://model.invalid/v1',
                                 http_client=sync, http_async_client=asynchronous)
        assert model.root_client.max_retries == model.root_async_client.max_retries == 0
    elif provider == 'anthropic':
        import langchain_anthropic.chat_models as wrapper
        monkeypatch.setattr(wrapper, '_get_default_httpx_client', lambda **kw: sync)
        monkeypatch.setattr(wrapper, '_get_default_async_httpx_client', lambda **kw: asynchronous)
        model = MeteredChatAnthropic(**options, base_url='https://model.invalid')
        assert model._client.max_retries == model._async_client.max_retries == 0
    else:
        from google import genai
        from google.genai.types import HttpOptions
        import langchain_google_genai.chat_models as wrapper
        def client(**kwargs):
            kwargs['http_options'] = HttpOptions(base_url='https://model.invalid',
                httpx_client=sync, httpx_async_client=asynchronous)
            return genai.Client(**kwargs)
        monkeypatch.setattr(wrapper, 'Client', client)
        model = MeteredChatGoogle(**options)
    assert model.max_retries == 0
    return model


async def consume(model, mode):
    if mode == 'invoke':
        model.invoke('controlled request')
    elif mode == 'ainvoke':
        await model.ainvoke('controlled request')
    elif mode == 'stream':
        list(model.stream('controlled request'))
    else:
        async for _ in model.astream('controlled request'):
            pass


@pytest.mark.parametrize('provider', ['openai', 'anthropic', 'google'])
@pytest.mark.parametrize('mode', ['invoke', 'ainvoke', 'stream', 'astream'])
@pytest.mark.parametrize('problem,expected_kind,attempts', [
    ('connection', 'connection', 3), ('timeout', 'timeout', 3),
    (429, 'rate_limit', 3), (503, 'service_error', 3),
    (401, 'authentication', 1), (403, 'authentication', 1),
    (402, 'quota', 1), ('quota', 'quota', 1), (400, 'service_error', 1),
])
def test_provider_failures_keep_retry_and_accounting_boundaries(
        tmp_path, monkeypatch, provider, mode, problem, expected_kind, attempts):
    monkeypatch.setattr(model_runtime, 'RETRY_DELAYS', (0, 0))
    sent = []
    def handle(request):
        sent.append(request)
        if problem in {'connection', 'timeout'}:
            error = httpx2.ConnectError if problem == 'connection' else httpx2.ReadTimeout
            raise error('fixture-secret-key must not be saved', request=request)
        status = 429 if problem == 'quota' else problem
        body = {'error': {'message': 'fixture-secret-key must not be saved',
                         'code': 'insufficient_quota' if problem == 'quota' else status,
                         'type': 'insufficient_quota' if problem == 'quota' else 'api_error',
                         'status': 'RESOURCE_EXHAUSTED' if status == 429 else 'UNKNOWN'}}
        return httpx2.Response(status, json=body)
    async def run():
        with httpx2.Client(transport=httpx2.MockTransport(handle)) as sync:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as asynchronous:
                model = model_with_transport(provider, sync, asynchronous, monkeypatch)
                with budget_scope(tmp_path):
                    with pytest.raises(ModelServiceError):
                        await consume(model, mode)
    asyncio.run(run())
    runtime = ResearchRuntime(tmp_path)
    rows = runtime.requests()
    assert len(sent) == len(rows) == runtime.status()['used'] == attempts
    assert {row['diagnostic']['error_kind'] for row in rows} == {expected_kind}
    assert 'fixture-secret-key' not in str(rows)
    if attempts == 1 and expected_kind in {'authentication', 'quota'}:
        assert runtime.stop_reason()['kind'] == expected_kind


@pytest.mark.parametrize('provider', ['openai', 'anthropic', 'google'])
@pytest.mark.parametrize('mode', ['stream', 'astream'])
def test_partial_provider_stream_is_not_replayed(tmp_path, monkeypatch, provider, mode):
    monkeypatch.setattr(model_runtime, 'RETRY_DELAYS', (0, 0))
    frames = {
        'openai': b'data: {"id":"partial","object":"chat.completion.chunk","created":0,"model":"controlled","choices":[{"index":0,"delta":{"role":"assistant","content":"already emitted"},"finish_reason":null}]}\n\n',
        'anthropic': (
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"partial","type":"message","role":"assistant","model":"controlled","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":0}}}\n\n'
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"already emitted"}}\n\n'),
        'google': b'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"already emitted"}]},"index":0}]}\n\n',
    }
    class BrokenSync(httpx2.SyncByteStream):
        def __iter__(self):
            yield frames[provider]
            raise httpx2.ReadError('controlled interruption')
    class BrokenAsync(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield frames[provider]
            raise httpx2.ReadError('controlled interruption')
    sent, chunks = [], []
    def handle(request):
        sent.append(request)
        return httpx2.Response(200, headers={'content-type': 'text/event-stream'},
                               stream=BrokenSync() if mode == 'stream' else BrokenAsync())
    async def run():
        with httpx2.Client(transport=httpx2.MockTransport(handle)) as sync:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as asynchronous:
                model = model_with_transport(provider, sync, asynchronous, monkeypatch)
                with budget_scope(tmp_path):
                    with pytest.raises(ModelServiceError):
                        if mode == 'stream':
                            for chunk in model.stream('partial request'):
                                chunks.append(chunk)
                        else:
                            async for chunk in model.astream('partial request'):
                                chunks.append(chunk)
    asyncio.run(run())
    assert 'already emitted' in str([chunk.content for chunk in chunks])
    assert len(sent) == ResearchRuntime(tmp_path).status()['used'] == 1
