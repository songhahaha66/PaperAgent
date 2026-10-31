"""Plan updates survive Agent boundaries and follow real export receipts."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

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
