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
    # Older manifests bound both the working name and published name to one archive.
    archived = next(meta for path, meta in ctx.fs._manifest['files'].items()
                    if path.startswith('outputs/research/') and path.endswith('/result.csv'))
    ctx.fs.write_text('outputs/result.csv', 'value\n6\n')
    ctx.fs._manifest['files']['outputs/result.csv'] = deepcopy(archived)
    ctx.fs._save_manifest()
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
    assert projected['detail'] == f"已保留 {len(projected['files'])} 个结果文件。" and not projected['error_file']
    assert ctx.fs.read_text(projected['output_file']).strip() == '6'
    assert any(path.endswith('/result.csv') for path in projected['files'])
    aliases = [(path, meta['archive_path']) for path, meta in ctx.fs._manifest['files'].items()
               if meta.get('source_run') == result['run_id'] and meta.get('archive_path') and path.endswith('/result.csv')]
    assert len(aliases) > 1, 'the same result is published through working and immutable aliases'
    assert len([path for path in projected['files'] if path.endswith('/result.csv')]) == 1
    assert all(path.startswith('outputs/research/') for path in projected['files'])
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


@pytest.mark.parametrize('mode', ['compile', 'cached', 'historical', 'failed'])
def test_pdf_preview_metadata_survives_stream_and_history_without_page_payloads(tmp_path, monkeypatch, mode):
    from unittest.mock import AsyncMock
    from ai_system.modules.writing import document
    ctx, history, reply, updates = conversation(tmp_path)
    version, pdf_hash = 'a' * 64, 'b' * 64
    warning = 'Overfull box at /private/build/paper.tex; api_key=secret-value'
    build = {'pdf_version': version, 'pdf_sha256': pdf_hash,
             'compile': {'success': mode != 'failed', 'state': 'current', 'warnings': [warning]}}
    monkeypatch.setattr(document.project, 'snapshot', lambda _: {'main': 'paper.tex', 'version': version})
    monkeypatch.setattr(document.project, 'meta', lambda _: build if mode in {'cached', 'historical'} else {})
    compiler = AsyncMock(return_value=build)
    monkeypatch.setattr(document.project, 'compile_project', compiler)
    rendered = []
    def render(*args):
        rendered.append(args)
        return {'pages': [{'page': p, 'text': 'PRIVATE PAGE TEXT'} for p in args[-1]],
                'total_pages': 17, 'pdf_sha256': pdf_hash, 'warnings': [warning],
                '_images': [{'path': 'PRIVATE IMAGE REFERENCE'}]}
    monkeypatch.setattr(document, 'render_pages', render)
    async def run():
        token = TIMELINE.set(reply)
        try:
            await invoke(ctx, 'writing.preview', pages=[6, 7, 8, 9],
                         **({'source_version': version, 'pdf_sha256': pdf_hash} if mode == 'historical' else {}))
            reply.finish('completed')
        finally:TIMELINE.reset(token)
    asyncio.run(run())
    assert compiler.await_count == (1 if mode in {'compile', 'failed'} else 0)
    activities = ctx.research.snapshot()['activity']
    assert len(activities) == 1
    assert 'PRIVATE' not in str(activities)
    stored = history.get_messages('work')[0]
    segment = stored['segments'][0]
    row = segment['execution']
    assert row['status'] == ('failed' if mode == 'failed' else 'completed')
    assert '第 6–9 页' in row['detail']
    assert 'secret-value' not in str(row) and '/private/build' not in str(row)
    assert row['warnings'] and 'Overfull' in row['warnings'][0]
    if mode == 'failed':
        assert not rendered and '共 17 页' not in row['detail'] and row['message']
    else:
        assert '共 17 页' in row['detail'] and '1 条编译警告' in row['detail']
    if mode == 'historical':assert '历史版本' in row['detail']
    streamed = [s['execution'] for update in updates for s in update['segments'] if s['type'] == 'execution']
    assert {s['id'] for s in streamed} == {row['id']}
    if mode in {'compile', 'failed'}:assert any('正在编译 LaTeX' in s['detail'] for s in streamed)
    if mode != 'failed':assert any('正在读取 PDF 页面' in s['detail'] for s in streamed)
    assert public_reply(stored, root=ctx.workspace)['segments'][0]['execution'] == row
    resolved = resolve_executions(ctx.workspace, stored, [segment['id']])[0]
    assert row == {key: value for key, value in resolved.items() if key != 'segment_id'}


@pytest.mark.parametrize('pages', [None, 3, 'invalid'])
def test_invalid_preview_inputs_still_leave_a_failed_execution(tmp_path, pages):
    from pydantic import ValidationError
    ctx, _, reply, _ = conversation(tmp_path)
    async def run():
        token = TIMELINE.set(reply)
        try:
            with pytest.raises(ValidationError):await invoke(ctx, 'writing.preview', pages=pages)
            reply.finish('completed')
        finally:TIMELINE.reset(token)
    asyncio.run(run())
    assert reply.reply['segments'][0]['execution']['status'] == 'failed'


def test_writing_file_receipts_keep_links_without_manuscript_text(tmp_path):
    ctx, history, reply, _ = conversation(tmp_path)
    async def run():
        token = TIMELINE.set(reply)
        try:
            result = await invoke(ctx, 'writing.edit', path='paper.md', content='PRIVATE MANUSCRIPT')
            await invoke(ctx, 'writing.edit', path='paper.md', old_text='PRIVATE', content='REPLACED',
                         expected_version=result['version'])
            ctx.fs.write_text('outputs/chart.png', 'figure bytes')
            ctx.fs.register_file('outputs/chart.png', kind='data', visibility='user')
            await invoke(ctx, 'writing.use_asset', source='outputs/chart.png', destination='figures/chart.png')
            reply.finish('completed')
        finally:TIMELINE.reset(token)
    asyncio.run(run())
    assert 'MANUSCRIPT' not in str(ctx.research.snapshot()['activity'])
    stored = public_reply(history.get_messages('work')[0], root=ctx.workspace)
    rows = [segment['execution'] for segment in stored['segments']]
    assert [row['file'] for row in rows] == ['paper.md', 'paper.md', 'figures/chart.png']
    assert all(row['status'] == 'completed' for row in rows)
    assert '局部修改' in rows[1]['detail'] and 'outputs/chart.png' in rows[2]['detail']
