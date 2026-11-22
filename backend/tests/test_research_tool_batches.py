"""Run tool batches through the normal research entry point with a scripted model."""
import asyncio
import json
import logging
from collections import Counter
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from ai_system.agent.store import AgentStore
from ai_system.modules.registry import CapabilityProduct, capabilities
from ai_system.research.session import resume, run_research
from ai_system.research.store import digest
from helpers.context import session_context
from research_tools_helpers import ResearchToolsModel, tool


def read(call_id):
    return {'name': 'research_read_artifact', 'args': {'path': call_id}, 'id': call_id}


def write(call_id):
    return {'name': 'workspace_edit', 'args': {'path': 'code/batch.py', 'content': 'marker = 1\n'}, 'id': call_id}


def research_batch(root, calls):
    received = []

    def respond(messages):
        results = [message for message in messages if isinstance(message, ToolMessage)]
        if not results:
            return AIMessage(id='batch', content='', tool_calls=calls)
        received.extend(results)
        return tool('research_pause', {'message': '本轮测试结束。'}, 'end')

    return session_context(root, ResearchToolsModel(respond=respond)), received


def replace_handler(monkeypatch, name, handler):
    registry = capabilities()
    capability = registry.get(name)
    monkeypatch.setitem(registry._capabilities, name, replace(capability, execute=handler))


def test_batch_overlaps_readers_and_orders_model_results_around_write(tmp_path, monkeypatch, caplog):
    ctx, received = research_batch(tmp_path, [read('A'), read('B'), write('C'), read('D')])
    timeline = []
    b_finished = asyncio.Event()
    edit = capabilities().get('workspace.edit').execute

    async def read_artifact(ctx, args):
        name = args['path']
        timeline.append(('start', name))
        if name == 'A':
            await asyncio.wait_for(b_finished.wait(), 2)
        timeline.append(('end', name))
        if name == 'B':
            b_finished.set()
            raise RuntimeError('Reader B failed')
        return CapabilityProduct('research_state', {'path': name})

    async def edit_file(ctx, args):
        timeline.append(('start', 'C'))
        result = await edit(ctx, args)
        timeline.append(('end', 'C'))
        return result

    replace_handler(monkeypatch, 'research.read_artifact', read_artifact)
    replace_handler(monkeypatch, 'workspace.edit', edit_file)
    with caplog.at_level(logging.INFO, logger='ai_system.agent.tools'):
        asyncio.run(run_research(ctx))

    assert timeline == [('start', 'A'), ('start', 'B'), ('end', 'B'), ('end', 'A'),
                        ('start', 'C'), ('end', 'C'), ('start', 'D'), ('end', 'D')]
    assert (tmp_path / 'code/batch.py').read_text() == 'marker = 1\n'
    assert [(m.tool_call_id, m.status) for m in received] == [
        ('A', 'success'), ('B', 'error'), ('C', 'success'), ('D', 'success')]
    assert json.loads(received[0].content)['path'] == 'A'
    assert received[1].content == 'Reader B failed'
    assert json.loads(received[3].content)['path'] == 'D'
    store = AgentStore(tmp_path)
    receipt = store.receipt('sessions-lead', 'research-tool:sessions:batch:B',
                            digest(['research.read_artifact', {'path': 'B'}]))
    assert receipt['status'] == 'failed' and receipt['value'] == 'Reader B failed'
    for call_id in 'ABCD':
        assert f':batch:{call_id} queue_seconds=' in caplog.text
    assert caplog.text.count('execution_seconds=') == 5
    assert 'message_id=batch calls=4 wall_seconds=' in caplog.text


@pytest.mark.parametrize(('control', 'status'), [('research_pause', 'paused'), ('research_ask_user', 'waiting')])
def test_batch_skips_calls_after_user_control(tmp_path, monkeypatch, control, status):
    stopping_call = {'name': control, 'args': {'message': '等待补充资料。'}, 'id': 'stop'}
    ctx, received = research_batch(tmp_path, [read('A'), stopping_call, write('C'), read('D')])
    reads = []

    async def read_artifact(ctx, args):
        reads.append(args['path'])
        return CapabilityProduct('research_state', {'path': args['path']})

    replace_handler(monkeypatch, 'research.read_artifact', read_artifact)
    result = asyncio.run(run_research(ctx))
    assert result['status'] == status
    assert ctx.stage_notes['solve'] == '等待补充资料。'
    store = AgentStore(tmp_path)
    assert store.session('sessions-lead')['status'] == status
    saved = [m for m in store.messages('sessions-lead') if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in saved] == ['A', 'stop', 'C', 'D']
    assert all(m.status == 'error' and m.additional_kwargs['skipped'] for m in saved[2:])
    assert {row['call_id'].rsplit(':', 1)[-1] for row in store.items()} == {'A', 'stop'}

    resume(ctx.research)
    asyncio.run(run_research(ctx))
    assert [m.tool_call_id for m in received] == ['A', 'stop', 'C', 'D']
    assert reads == ['A'] and not (tmp_path / 'code/batch.py').exists()


def test_batch_refills_four_slots_and_resumes_only_calls_without_receipts(tmp_path, monkeypatch):
    labels = 'ABCDEF'
    ctx, received = research_batch(tmp_path, [read(label) for label in labels])
    entered = {label: asyncio.Event() for label in labels}
    release = {label: asyncio.Event() for label in labels}
    calls = Counter()
    recovering = False

    async def read_artifact(ctx, args):
        label = args['path']
        calls[label] += 1
        entered[label].set()
        if not recovering:
            await release[label].wait()
        return CapabilityProduct('research_state', {'path': label})

    replace_handler(monkeypatch, 'research.read_artifact', read_artifact)

    async def run():
        nonlocal recovering
        research = asyncio.create_task(run_research(ctx))
        try:
            await asyncio.wait_for(entered['D'].wait(), 2)
            assert list(calls) == list('ABCD')
            release['B'].set()
            await asyncio.wait_for(entered['E'].wait(), 2)
            assert list(calls) == list('ABCDE')
            release['D'].set()
            await asyncio.wait_for(entered['F'].wait(), 2)
        finally:
            research.cancel()
            with pytest.raises(asyncio.CancelledError):
                await research

        store = AgentStore(tmp_path)
        assert store.session('sessions-lead')['status'] == 'interrupted'
        assert {row['call_id'].rsplit(':', 1)[-1] for row in store.items() if row['status'] == 'completed'} == {'B', 'D'}
        assert not any(isinstance(m, ToolMessage) for m in store.messages('sessions-lead'))
        recovering = True
        restarted = session_context(tmp_path, ctx.planner_llm)
        resume(restarted.research)
        await run_research(restarted)

    asyncio.run(run())
    assert calls == {'A': 2, 'B': 1, 'C': 2, 'D': 1, 'E': 2, 'F': 2}
    assert [m.tool_call_id for m in received] == list(labels)
    assert [json.loads(m.content)['path'] for m in received] == list(labels)
