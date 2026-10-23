from helpers.remote_files import RemoteFiles
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.sandbox.factory import create_kernel
from ai_system.sandbox.remote_kernel import RemoteKernel, safe_path
from ai_system.sandbox.settings import SandboxSettings
from ai_system.sandbox.contracts import ExecutionOutcome


class StreamingSession(RemoteFiles):
    """Current transfer protocol with deterministic, cancellable execution."""

    def __init__(self, workspace):
        super().__init__(workspace)
        self.ready = asyncio.Event()
        self.starts = self.closes = self.interrupts = 0

    async def start(self):
        await super().start()
        self.starts += 1

    async def execute(self, code, run_id, output):
        self.calls.append(code)
        await output('stdout', 'started\n')
        (self.remote / 'outputs/table.csv').write_text(code)
        self.ready.set()
        if code == 'wait':
            await asyncio.Event().wait()
        await output('stderr', 'warning\n')
        return ExecutionOutcome()

    async def interrupt(self):
        self.interrupts += 1
        await super().interrupt()

    async def close(self):
        self.closes += 1
        await super().close()


async def wait_for_execution(task, ready):
    """An early execution failure must never leave the test waiting for ready."""
    waiter = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait({task, waiter}, timeout=2,
                                     return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            result = await task
            pytest.fail(f'Execution finished before cancellation: {result.error}')
        assert waiter in done, 'Execution did not become ready within 2 seconds'
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


def test_remote_streams_persists_and_versions_files(tmp_path):
    async def run():
        session = StreamingSession(tmp_path)
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        events = []
        async def event(value):
            events.append(value)
            if value['type'] == 'output':
                assert (kernel.fs.run_dir(value['run_id']) / (value['channel'] + '.log')).read_text()
        first = await kernel.execute('value\n1\n', on_event=event)
        second = await kernel.execute('value\n2\n')
        await kernel.close()
        assert first.ok and second.ok and session.starts == 1 and session.closes == 1
        assert f'runs/{first.run_id}/artifacts/outputs/table.csv' in first.files
        assert kernel.fs.read_text(f'runs/{first.run_id}/artifacts/outputs/table.csv') == 'value\n1\n'
        assert (tmp_path / 'outputs/table.csv').read_text() == 'value\n2\n'
        assert any(e['type'] == 'output' for e in events)
        assert events[-1]['status'] == 'success'
        assert (kernel.fs.run_dir(first.run_id) / 'stderr.log').read_text() == 'warning\n'
    asyncio.run(run())


def test_cancel_terminates_and_finalizes_run(tmp_path):
    async def run():
        session = StreamingSession(tmp_path)
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        task = asyncio.create_task(kernel.execute('wait'))
        try:
            await wait_for_execution(task, session.ready)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            row = next(iter(kernel.fs._manifest['runs'].values()))
            assert row['status'] == 'cancelled' and row['finished_at']
            assert session.closes == session.interrupts == 1
            assert kernel.fs.read_text(row['stdout']) == 'started\n'
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await kernel.close()
    asyncio.run(run())


def test_cancel_wait_reports_startup_failure_instead_of_hanging(tmp_path):
    class BrokenSession(StreamingSession):
        async def start(self):
            raise RuntimeError('controlled startup failure')

    async def run():
        session = BrokenSession(tmp_path)
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        task = asyncio.create_task(kernel.execute('wait'))
        try:
            with pytest.raises(pytest.fail.Exception, match='controlled startup failure'):
                await wait_for_execution(task, session.ready)
            assert not session.ready.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await kernel.close()
    asyncio.run(run())


@pytest.mark.parametrize('path', ['/etc/passwd', '../secret', 'outputs/../../secret', '.system/key', 'outputs\\secret'])
def test_rejects_remote_paths(path):
    with pytest.raises(ValueError):
        safe_path(path)


def test_remote_never_falls_back_to_local(tmp_path):
    with pytest.raises(ValueError, match='未支持'):
        create_kernel(tmp_path, settings=SandboxSettings(provider='unknown'))


def test_corrupt_download_never_replaces_existing_output(tmp_path):
    class CorruptSession(StreamingSession):
        async def download(self, path, destination, max_bytes):
            destination.write_bytes(b'corrupt')
    async def run():
        session = CorruptSession(tmp_path)
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        path = tmp_path / 'outputs/table.csv'
        path.write_text('old')
        result = await kernel.execute('new')
        assert result.ok and result.delivery_error and path.read_text() == 'old'
        assert not path.with_name('table.csv.download').exists()
        await kernel.close()
    asyncio.run(run())


def test_opensandbox_callback_error_stops_even_when_sdk_swallows_handlers(tmp_path):
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
        session = OpenSandboxSession(
            SandboxSettings(host_workspace_root='/server-only/cache'), workspace_dir=tmp_path)
        session.interpreter = SimpleNamespace(codes=SimpleNamespace(run=sdk_run))
        with pytest.raises(ValueError, match='quota'):
            await asyncio.wait_for(session.execute('print(1)', 'run-test', output), 1)
        assert stopped.is_set()
    asyncio.run(run())
