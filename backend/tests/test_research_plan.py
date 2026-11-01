"""Plan updates survive Agent boundaries and follow real export receipts."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict

from ai_system.modules.registry import invoke
from ai_system.research.loop import prepare_action, _act
from ai_system.research.planner import working_context, decision_context
from ai_system.research.runtime import execution_scope
from ai_system.research.store import ResearchStore
from helpers.context import session_context
from research_tools_helpers import ResearchToolsModel, tool
from services.chat_services.research_session import research_snapshot
from services.file_services.workspace_layout import workspace_path


STEPS = [{'title': '读取原始数据', 'status': 'completed'},
         {'title': '计算与分析', 'status': 'in_progress'},
         {'title': '整理报告', 'status': 'pending'}]


def test_agent_updates_plan_and_continuation_and_public_snapshot_reuse_it(tmp_path):
    def respond(messages):
        rows = [m for m in messages if isinstance(m, ToolMessage)]
        if not rows:
            return tool('research_update_plan', {'steps': STEPS}, 'plan')
        assert json.loads(rows[-1].content)['progress_plan']['steps'][1]['status'] == 'in_progress'
        return tool('research_pause', {'message': '等待补充数据'}, 'pause')
    ctx = session_context(tmp_path, ResearchToolsModel(respond=respond))
    assert asyncio.run(prepare_action(ctx))
    state = ResearchStore(tmp_path, ctx.research.research_id).snapshot()
    plan = state['progress_plan']
    assert working_context(ctx)['progress_plan'] == plan
    assert decision_context(ctx.research, state)[1]['progress_plan'] == plan
    workspace_path(tmp_path, '.system/graph_thread.json').write_text(json.dumps({
        'thread_id': ctx.research.research_id, 'engine_version': 'research-v3'}))
    assert research_snapshot(tmp_path, 'work', 'paused').model_dump()['progress_plan'] == plan
    assert all(not state[key] for key in ('questions', 'experiments', 'executions', 'claims', 'artifacts'))


def test_plan_receipts_are_idempotent_and_identical_updates_keep_timestamp(tmp_path):
    ctx = session_context(tmp_path)
    async def scenario():
        with execution_scope(tool_call_id='plan-once'):
            first = await invoke(ctx, 'research.update_plan', steps=STEPS)
            assert await invoke(ctx, 'research.update_plan', steps=STEPS) == first
        unchanged = await invoke(ctx, 'research.update_plan', steps=STEPS)
        assert unchanged['unchanged'] and unchanged['revision'] == first['revision']
        assert unchanged['progress_plan'] == first['progress_plan']
        changed = [dict(s) for s in STEPS]
        changed[1]['status'] = 'completed'
        changed[2]['status'] = 'blocked'
        changed[2]['note'] = '需要用户补充数据范围'
        result = await invoke(ctx, 'research.update_plan', steps=changed)
        assert result['revision'] == first['revision'] + 1
        assert result['progress_plan']['steps'][2]['note'] == changed[2]['note']
    asyncio.run(scenario())


@pytest.mark.parametrize('invalid', [
    [{'title': '   ', 'status': 'pending'}],
    [{'title': '重复', 'status': 'pending'}] * 2,
    [{'title': 'A', 'status': 'in_progress'}, {'title': 'B', 'status': 'in_progress'}],
    [{'title': '导出', 'status': 'pending', 'kind': 'delivery'}, {'title': '其他', 'status': 'pending'}],
])
def test_invalid_plan_does_not_replace_saved_progress(tmp_path, invalid):
    ctx = session_context(tmp_path)
    asyncio.run(invoke(ctx, 'research.update_plan', steps=STEPS))
    before = ctx.research.snapshot()
    with pytest.raises(ValueError):
        asyncio.run(invoke(ctx, 'research.update_plan', steps=invalid))
    after = ctx.research.snapshot()
    assert after['progress_plan'] == before['progress_plan']
    assert after['revision'] == before['revision']


def test_export_failure_and_retry_update_only_explicit_delivery_item(tmp_path):
    ctx = session_context(tmp_path)
    # A pending ordinary item must never become completed just because export succeeded.
    steps = [{'title': '补充分析', 'status': 'pending'},
             {'title': '导出论文', 'status': 'pending', 'kind': 'delivery'}]
    (tmp_path / 'paper.md').write_text('# 报告\n![图](missing.png)')
    async def scenario():
        await invoke(ctx, 'research.update_plan', steps=steps)
        for broken in (True, False):
            if not broken:
                (tmp_path / 'paper.md').write_text('# 报告\n已有结果；补充分析尚未完成。')
            await invoke(ctx, 'research.deliver')
            action = ctx.research.snapshot()['action']
            await _act({'action_id': action['id']}, SimpleNamespace(context=ctx))
            plan = ctx.research.snapshot()['progress_plan']
            assert plan['steps'][0]['status'] == 'pending'
            assert plan['steps'][-1]['status'] == ('blocked' if broken else 'completed')
        assert ctx.research.snapshot()['outcome'] == 'completed'
    asyncio.run(scenario())


def test_stale_plan_reminder_counts_tools_in_parallel_batches_and_resets_only_on_success(tmp_path):
    (tmp_path / 'source.txt').write_text('0123456789')
    changed = [dict(step) for step in STEPS]
    changed[1]['status'] = 'completed'
    changed[2]['status'] = 'in_progress'
    seen = []

    def respond(messages):
        rows = [message for message in messages if isinstance(message, ToolMessage)]
        reminders = [message for message in messages if message.additional_kwargs.get('plan_reminder')]
        seen.append(len(rows))
        if len(rows) in {11, 12}:
            assert reminders == [messages[-1]]
            assert '计划可能已过期' in messages[-1].content
            assert json.loads(messages[-1].content.split('当前计划：', 1)[1].split('\n', 1)[0]) == ctx.research.snapshot()['progress_plan']['steps']
        else:
            assert not reminders
        if not rows:
            return tool('research_update_plan', {'steps': STEPS}, 'plan')
        if len(rows) == 1:
            return AIMessage('', tool_calls=[{'name': 'research_read_artifact', 'id': f'read-{i}',
                'args': {'path': 'source.txt', 'start': i, 'limit': 1}} for i in range(9)])
        if len(rows) == 10:
            return tool('research_read_artifact', {'path': 'source.txt', 'start': 9, 'limit': 1}, 'read-9')
        if len(rows) == 11:
            return tool('research_update_plan', {'steps': [
                {'title': 'A', 'status': 'in_progress'}, {'title': 'B', 'status': 'in_progress'}]}, 'bad-plan')
        if len(rows) == 12:
            assert rows[-1].status == 'error'
            return tool('research_update_plan', {'steps': changed}, 'fresh-plan')
        assert len(rows) == 13
        return tool('research_pause', {'message': '等待补充报告材料'}, 'pause')

    model = ResearchToolsModel(respond=respond)
    ctx = session_context(tmp_path, model)
    assert asyncio.run(prepare_action(ctx))
    assert seen == [0, 1, 10, 11, 12, 13] and model.calls == 6
    transcript = next(workspace_path(tmp_path, '.system/transcripts').glob('*.json')).read_text()
    assert 'plan_reminder' not in transcript and '计划可能已过期' not in transcript


def test_plan_reminder_survives_context_compaction_and_unchanged_update_clears_it(tmp_path):
    from ai_system.graph.compaction import ContextMiddleware
    from ai_system.research.plan import plan_reminder
    from helpers.context import ContextRequest

    model = ResearchToolsModel(respond=lambda _: AIMessage('{"notes":["沿用既定分析目标与已保存结果。"]}'),
                               profile={'max_input_tokens': 15000})
    ctx = session_context(tmp_path, model)
    initial = asyncio.run(invoke(ctx, 'research.update_plan', steps=STEPS))
    history = [HumanMessage('读取数据，分析后整理报告。'), tool('research_update_plan', {'steps': STEPS}, 'plan'),
               ToolMessage(json.dumps(initial), tool_call_id='plan', name='research_update_plan')]
    for i in range(10):
        history.extend([tool('research_run_python', {'code': '分析' * 800}, str(i)),
                        ToolMessage('已有结果 ' + str(i), tool_call_id=str(i), name='research_run_python')])
    original = messages_to_dict(history)
    sent = []
    async def handle(request):
        sent.append(request.messages)
    async def project():
        return await ContextMiddleware(ctx, model, 'plan-reminder', pinned={},
            tail=lambda messages: plan_reminder(ctx, messages)).awrap_model_call(ContextRequest(history), handle)
    asyncio.run(project())
    with ctx.research._connect() as db:
        saved = json.loads(db.execute('SELECT response FROM commands WHERE id=?', ('context:plan-reminder',)).fetchone()[0])
    assert saved['cut'] > 2 and sent[-1][-1].additional_kwargs['plan_reminder']
    assert '10 次工具调用' in sent[-1][-1].content
    assert messages_to_dict(history) == original
    calls = model.calls
    # A new middleware instance reuses the checkpoint and counts original history.
    asyncio.run(project())
    assert model.calls == calls and messages_to_dict(sent[0]) == messages_to_dict(sent[1])
    refreshed = asyncio.run(invoke(ctx, 'research.update_plan', steps=STEPS))
    assert refreshed['unchanged'] and refreshed['progress_plan']['updated_at'] == initial['progress_plan']['updated_at']
    history.extend([tool('research_update_plan', {'steps': STEPS}, 'refresh'),
                    ToolMessage(json.dumps(refreshed), tool_call_id='refresh', name='research_update_plan')])
    asyncio.run(project())
    assert not any(message.additional_kwargs.get('plan_reminder') for message in sent[-1])
    assert model.calls == calls
