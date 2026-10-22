from helpers.sandbox import FakeSession
import asyncio
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.sandbox.contracts import ExecutionOutcome, SandboxFile
from ai_system.sandbox.factory import create_kernel
from ai_system.sandbox.remote_kernel import RemoteKernel, safe_path
from ai_system.sandbox.settings import SandboxSettings
from ai_system.sandbox.artifacts import publish_run_files
from services.file_services.workspace_fs import WorkspaceFS


def test_remote_streams_persists_and_versions_files(tmp_path):
    async def run():
        session = FakeSession()
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        events = []
        async def event(value):
            events.append(value)
            if value['type'] == 'output':
                assert (kernel.fs.run_dir(value['run_id']) / (value['channel'] + '.log')).read_text()
        first = await kernel.execute('value\n1\n', on_event=event)
        second = await kernel.execute('value\n2\n')
        await kernel.close()
        assert first.ok and second.ok and session.started == 1 and session.closed == 1
        assert 'outputs/table.csv' in first.files
        assert (tmp_path / 'runs' / first.run_id / 'artifacts/outputs/table.csv').read_text() == 'value\n1\n'
        assert (tmp_path / 'outputs/table.csv').read_text() == 'value\n2\n'
        assert any(e['type'] == 'output' for e in events)
        assert events[-1]['status'] == 'success'
        assert (tmp_path / 'runs' / first.run_id / 'stderr.log').read_text() == 'warning\n'
    asyncio.run(run())


@pytest.mark.parametrize('code', ['error', 'disconnect', 'wait', 'verbose'])
def test_failure_preserves_logs_and_files_without_reexecution(tmp_path, code):
    async def run():
        session = FakeSession()
        settings = SandboxSettings(provider='fake', max_log_bytes=30)
        kernel = RemoteKernel(tmp_path, session, settings, timeout=.05)
        result = await kernel.execute(code)
        assert not result.ok
        assert session.calls == [code]
        assert 'started' in (tmp_path / 'runs' / result.run_id / 'stdout.log').read_text()
        assert (tmp_path / 'outputs/table.csv').exists()
        assert kernel.fs.get_run_info(result.run_id)['status'] in {'error', 'timeout'}
        if code != 'error':
            assert session.interrupted == session.closed == 1
        await kernel.close()
    asyncio.run(run())


def test_cancel_terminates_and_finalizes_run(tmp_path):
    async def run():
        session = FakeSession()
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        task = asyncio.create_task(kernel.execute('wait'))
        await session.ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        row = next(iter(kernel.fs._manifest['runs'].values()))
        assert row['status'] == 'cancelled' and row['finished_at']
        assert session.closed == session.interrupted == 1
        assert (tmp_path / row['stdout']).read_text() == 'started\n'
    asyncio.run(run())


@pytest.mark.parametrize('path', ['/etc/passwd', '../secret', 'outputs/../../secret', '.system/key', 'outputs\\secret'])
def test_rejects_remote_paths(path):
    with pytest.raises(ValueError):
        safe_path(path)


def test_remote_never_falls_back_to_local(tmp_path):
    with pytest.raises(ValueError, match='未支持'):
        create_kernel(tmp_path, settings=SandboxSettings(provider='unknown'))


def test_config_is_explicit_and_hides_key(monkeypatch, tmp_path):
    path = tmp_path / 'config.env'
    path.write_text('PAPERAGENT_SANDBOX_PROVIDER=opensandbox\nPAPERAGENT_SANDBOX_API_KEY=test-secret\nPAPERAGENT_SANDBOX_MAX_LOG_BYTES=4096\n')
    monkeypatch.setenv('PAPERAGENT_SANDBOX_CONFIG', str(path))
    monkeypatch.setenv('PAPERAGENT_SANDBOX_PROVIDER', 'opensandbox')
    settings = SandboxSettings.from_env()
    assert settings.max_log_bytes == 4096
    assert 'test-secret' not in repr(settings)
    monkeypatch.setenv('PAPERAGENT_SANDBOX_ENDPOINT', 'http://remote.example')
    with pytest.raises(ValueError, match='HTTPS'):
        SandboxSettings.from_env()


def test_files_publish_even_if_computation_failed(tmp_path):
    async def run():
        kernel = RemoteKernel(tmp_path / 'attempt', FakeSession(), SandboxSettings(provider='fake'))
        result = await kernel.execute('error')
        target = WorkspaceFS(str(tmp_path / 'public'))
        target.init_structure()
        paths = publish_run_files(kernel.fs, target, 'attempt-1')
        assert not result.ok and len(paths) == 1
        assert target.abs(paths[0]).read_text() == 'error'
        assert target.get_file_meta(paths[0])['source_run'] == result.run_id
        assert publish_run_files(kernel.fs, target, 'attempt-1') == []
        await kernel.close()
    asyncio.run(run())


def test_syncs_changes_between_cells_without_reuploading_unchanged_files(tmp_path):
    async def run():
        session = FakeSession()
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        await kernel.execute('first')
        path = tmp_path / 'code/new.py'
        path.write_text('x=1')
        await kernel.execute('second')
        assert session.content['code/new.py'] == b'x=1'
        path.unlink()
        await kernel.execute('third')
        assert 'code/new.py' not in session.content
        await kernel.close()
    asyncio.run(run())


def test_corrupt_download_never_replaces_existing_output(tmp_path):
    class CorruptSession(FakeSession):
        async def download(self, path, destination, max_bytes):
            destination.write_bytes(b'corrupt')
    async def run():
        session = CorruptSession()
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        path = tmp_path / 'outputs/table.csv'
        path.write_text('old')
        result = await kernel.execute('new')
        assert result.ok and result.delivery_error and path.read_text() == 'old'
        assert not path.with_name('table.csv.download').exists()
        await kernel.close()
    asyncio.run(run())


def test_missing_configuration_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv('PAPERAGENT_SANDBOX_CONFIG', str(tmp_path / 'absent.env'))
    with pytest.raises(ValueError, match='不会回退'):
        SandboxSettings.from_env()


def test_debian_noto_collection_is_selected_for_chinese_figures():
    from ai_system.sandbox.runtime_helpers import choose_font
    assert choose_font({'DejaVu Sans', 'Noto Sans CJK JP'}) == 'Noto Sans CJK JP'
    assert choose_font({'Noto Sans CJK SC', 'Noto Sans CJK JP'}) == 'Noto Sans CJK SC'
    assert choose_font({'DejaVu Sans'}) == 'DejaVu Sans'


def test_remote_nested_figure_promotes_immutable_run_copy(tmp_path):
    from ai_system.graph.registry import ResultRegistry, GroundingError
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run = fs.create_run()
    archive = fs.register_run_artifact(run, 'outputs/distribution.png')
    fs.write_text(archive, 'original image')
    fs.write_text('outputs/distribution.png', 'later overwritten image')
    fs.finish_run(run, 'success')
    registry = ResultRegistry(fs)
    for reference in (archive, 'outputs/distribution.png'):
        result = registry.record_figure('E1', run, reference, '分布图')
        assert fs.read_text(result.path) == 'original image'
    for reference in ('../other.png', '/tmp/other.png', 'runs/another/artifacts/other.png'):
        with pytest.raises(GroundingError):
            registry.record_figure('E1', run, reference, '拒绝跨运行路径')


def test_opensandbox_callback_error_stops_even_when_sdk_swallows_handlers():
    pytest.importorskip('opensandbox')
    from types import SimpleNamespace
    from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
    async def run():
        stopped = asyncio.Event()
        async def sdk_run(code, context, handlers):
            try:
                await handlers.on_stdout(SimpleNamespace(text='too much'))
                await asyncio.sleep(10)
            finally:
                stopped.set()
        async def output(channel, text):
            raise ValueError('quota')
        session = OpenSandboxSession(SandboxSettings())
        session.interpreter = SimpleNamespace(codes=SimpleNamespace(run=sdk_run))
        with pytest.raises(ValueError, match='quota'):
            await asyncio.wait_for(session.execute('print(1)', 'run-test', output), 1)
        assert stopped.is_set()
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['error', 'disconnect', 'missing_receipt'])
def test_opensandbox_keeps_calculation_receipt_when_saving_figures_fails(fault):
    pytest.importorskip('opensandbox')
    from types import SimpleNamespace
    from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
    async def run():
        calls = []
        async def sdk_run(code, **kwargs):
            calls.append(code)
            if code == 'calculate':
                return SimpleNamespace(complete=True, error=None, result=[])
            if fault == 'disconnect': raise ConnectionError('file channel offline')
            return SimpleNamespace(complete=None if fault == 'missing_receipt' else True,
                error=SimpleNamespace(traceback=['cannot save figure']) if fault == 'error' else None)
        async def output(*args): pass
        session = OpenSandboxSession(SandboxSettings())
        session.interpreter = SimpleNamespace(codes=SimpleNamespace(run=sdk_run))
        outcome = await session.execute('calculate', 'run-test', output)
        assert outcome.error is None and calls == ['calculate']
        with pytest.raises((RuntimeError, ConnectionError)):
            await session.finalize('run-test')
        assert calls.count('calculate') == 1
    asyncio.run(run())
