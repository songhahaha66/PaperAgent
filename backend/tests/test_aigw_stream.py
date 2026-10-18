"""Regression for cumulative usage observed on AIGW's real Step 5 SSE stream."""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.core_handlers.llm_providers import AIGWChatOpenAI, OpenAIProvider


def response(request):
    payload = json.loads(request.content)
    assert payload['max_tokens'] == 512
    assert 'max_completion_tokens' not in payload
    chunks = []
    for delta, prompt, completion, finish in [
        ({'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_check', 'type': 'function',
          'function': {'name': 'check', 'arguments': '{"value":'}}]}, 100, 1, None),
        ({'tool_calls': [{'index': 0, 'function': {'arguments': '10}'}}]}, 100, 3, None),
        # Some real streams briefly reset counters before the final snapshot.
        ({}, 0, 0, None),
        ({}, 100, 3, 'tool_calls'),
    ]:
        chunks.append('data: ' + json.dumps({
            'id': 'test', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'step-5-preview',
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
            'usage': {'prompt_tokens': prompt, 'completion_tokens': completion, 'total_tokens': prompt + completion,
                      'prompt_tokens_details': {'cached_tokens': 20},
                      'completion_tokens_details': {'reasoning_tokens': 1}},
        }))
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                          text='\n\n'.join(chunks) + '\n\ndata: [DONE]\n\n')


def check(message):
    assert message.tool_calls[0]['args'] == {'value': 10}
    assert message.usage_metadata == {
        'input_tokens': 100, 'output_tokens': 3, 'total_tokens': 103,
        'input_token_details': {'cache_read': 20}, 'output_token_details': {'reasoning': 1},
    }


def test_cumulative_usage_sync_stream_keeps_tool_arguments_and_final_totals():
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        llm = AIGWChatOpenAI(model='step-5-preview', api_key='fixture',
                            base_url='https://aigw.saurlax.com/v1', max_tokens=512, streaming=True, http_client=client)
        check(llm.invoke('check'))
        check(llm.invoke('check again'))


def test_cumulative_usage_async_stream_counters_are_isolated_per_request():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            llm = AIGWChatOpenAI(model='step-5-preview', api_key='fixture',
                                base_url='https://aigw.saurlax.com/v1', max_tokens=512, streaming=True, http_async_client=client)
            for message in await asyncio.gather(llm.ainvoke('first'), llm.ainvoke('second')):
                check(message)
    asyncio.run(run())


@pytest.mark.parametrize('host,expected', [('aigw.saurlax.com', True), ('api.openai.com', False),
                                           ('aigw.saurlax.com.example.org', False)])
def test_gateway_adapter_is_scoped_to_exact_host(host, expected):
    llm = OpenAIProvider({'model_id': 'step-5-preview', 'api_key': 'fixture',
                          'base_url': f'https://{host}/v1'}).create_llm_instance()
    assert isinstance(llm, AIGWChatOpenAI) is expected
