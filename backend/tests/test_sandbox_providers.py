from helpers.sandbox import FakeSession
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.sandbox.factory import create_kernel
from ai_system.sandbox.remote_kernel import RemoteKernel, safe_path
from ai_system.sandbox.settings import SandboxSettings


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
