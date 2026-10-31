"""Actual execution -> durable record -> stream/history/detail projections."""
import asyncio
from copy import deepcopy

import pytest

from ai_system.modules.registry import invoke
from ai_system.research.views import execution
from services.chat_services.chat_history_manager import ChatHistoryManager
from services.chat_services.conversation_timeline import ReplyWriter, TIMELINE, public_reply, resolve_executions
from helpers.context import session_context as context


def conversation(tmp_path):
    ctx = context(tmp_path / 'work')
    history = ChatHistoryManager(str(tmp_path))
    updates = []
    reply = ReplyWriter(history, 'work', 'task', ctx.workspace, updates.append)
    return ctx, history, reply, updates


@pytest.mark.usefixtures('offline_kernel')
def test_one_computation_has_one_record_across_stream_history_and_research(tmp_path):
    ctx, history, reply, updates = conversation(tmp_path)
    async def run():
        token = TIMELINE.set(reply)
        try:
            reply.text('先检查附件。')
            result = await invoke(ctx, 'research.run_python', title='检查附件与模板结构',
                                  code="from pathlib import Path\nprint(sum([1, 2, 3]))\nPath('outputs/result.csv').write_text('value\\n6\\n')")
            reply.text('检查完成。')
            reply.finish('completed')
            return result
        finally:
            TIMELINE.reset(token)
            await ctx.kernels.close()
    result = asyncio.run(run())
    assert len(ctx.fs._manifest['runs']) == 1
    activities = ctx.research.snapshot()['activity']
    assert len(activities) == 1
    assert activities[0]['run_id'] == result['run_id']
    stored = history.get_messages('work')[0]
    assert [s['type'] for s in stored['segments']] == ['text', 'execution', 'text']
    segment = stored['segments'][1]
    streamed = [s for update in updates for s in update['segments'] if s['type'] == 'execution']
    assert len({s['id'] for s in streamed}) == len({s['execution']['id'] for s in streamed}) == 1
    assert any(s['execution'].get('output_tail', '').strip() == '6' for s in streamed)
    assert all(s['execution']['status'] == 'running' for s in streamed[:-1])
    projected = execution(ctx.research.snapshot(), 'completed', ctx.workspace)['actions'][0]
    resolved = resolve_executions(ctx.workspace, stored, [segment['id']])[0]
    assert projected == {k: v for k, v in resolved.items() if k != 'segment_id'} == segment['execution']
    assert projected['label'] == '检查附件与模板结构' and projected['status'] == 'completed'
    assert not projected['detail'] and not projected['error_file']
    assert ctx.fs.read_text(projected['output_file']).strip() == '6'
    assert any(path.endswith('/result.csv') for path in projected['files'])
    stale = deepcopy(stored)
    stale['segments'][1]['execution'].update(status='running', label='旧缓存')
    assert public_reply(stale, root=ctx.workspace)['segments'][1]['execution'] == projected
    assert resolve_executions(ctx.workspace, stored, ['unknown-segment']) == []


@pytest.mark.usefixtures('offline_kernel')
def test_real_retry_preserves_failed_attempt_and_warning_is_not_failure(tmp_path):
    ctx, _, reply, _ = conversation(tmp_path)
    async def run():
        token = TIMELINE.set(reply)
        try:
            await invoke(ctx, 'research.run_python', title='计算统计量', code="raise ValueError('bad data')")
            await invoke(ctx, 'research.run_python', title='重新计算统计量',
                         code="import sys\nprint('warning: missing optional column', file=sys.stderr)\nprint(sum([2, 3]))")
            reply.finish('completed')
        finally:
            TIMELINE.reset(token)
            await ctx.kernels.close()
    asyncio.run(run())
    rows = [s['execution'] for s in reply.reply['segments'] if s['type'] == 'execution']
    assert len(rows) == len(ctx.fs._manifest['runs']) == 2
    assert rows[0]['run_id'] != rows[1]['run_id']
    assert [r['status'] for r in rows] == ['failed', 'completed']
    assert 'bad data' in rows[0]['message']
    assert 'warning' in ctx.fs.read_text(rows[1]['error_file'])


@pytest.mark.parametrize('failure', ['invalid_input', 'invalid_question', 'placeholder', 'executor'])
def test_preparation_and_handled_tool_errors_never_look_completed(tmp_path, failure):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    ctx, _, reply, _ = conversation(tmp_path)
    args = {'code': 'print(sum([1, 2]))'}
    if failure == 'invalid_input':args['code'] = ''
    if failure == 'invalid_question':args['question_id'] = 'missing'
    if failure == 'placeholder':args['code'] = 'pass'
    if failure == 'executor':
        ctx.kernels._kernels['research'] = SimpleNamespace(execute=AsyncMock(side_effect=ValueError('cannot submit')))
    async def run():
        token = TIMELINE.set(reply)
        try:
            try:
                await invoke(ctx, 'research.run_python', **args)
            except ValueError:
                assert failure in {'invalid_input', 'invalid_question'}
            reply.finish('completed')
        finally:TIMELINE.reset(token)
    asyncio.run(run())
    rows = [s['execution'] for s in reply.reply['segments'] if s['type'] == 'execution']
    assert len(rows) == 1 and rows[0]['status'] == 'failed'
    assert rows[0]['message'] and not ctx.fs._manifest['runs']


@pytest.mark.parametrize('recover', [True, False])
def test_internal_file_retry_keeps_same_record_and_never_recomputes(tmp_path, recover):
    from helpers.sandbox import FakeSession
    from ai_system.sandbox.remote_kernel import RemoteKernel
    from ai_system.sandbox.settings import SandboxSettings
    ctx, _, reply, _ = conversation(tmp_path)
    class Files(FakeSession):
        failures = 0
        async def download(self, *args, **kwargs):
            if not recover or self.failures < 2:
                self.failures += 1
                raise ConnectionError('file channel unavailable')
            return await super().download(*args, **kwargs)
    session = Files(ctx.workspace)
    ctx.kernels._kernels['research'] = RemoteKernel(ctx.workspace, session, SandboxSettings(provider='fake'), fs=ctx.fs)
    async def run():
        token = TIMELINE.set(reply)
        try:
            await invoke(ctx, 'research.run_python', code='print(42)', title='保存结果文件')
            reply.finish('completed')
        finally:
            TIMELINE.reset(token)
            await ctx.kernels.close()
    asyncio.run(run())
    rows = [s['execution'] for s in reply.reply['segments'] if s['type'] == 'execution']
    assert len(rows) == len(session.calls) == 1
    assert rows[0]['status'] == ('completed' if recover else 'interrupted')
    if not recover:assert '文件交付未完成' in rows[0]['message']


@pytest.mark.usefixtures('offline_kernel')
def test_parallel_calls_keep_their_own_context_and_run(tmp_path):
    ctx = context(tmp_path)
    async def run():
        try:
            return await asyncio.gather(*(invoke(ctx, 'research.run_python', title=f'计算{i}',
                                               code=f'print(sum([{i}, 1]))') for i in range(2)))
        finally:await ctx.kernels.close()
    results = asyncio.run(run())
    rows = ctx.research.snapshot()['activity']
    assert len(rows) == len({row['id'] for row in rows}) == 2
    assert {(r['display_label'],r['run_id']) for r in rows} == {(f'计算{i}',r['run_id']) for i,r in enumerate(results)}
