from helpers.remote_files import RemoteFiles
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.sandbox.remote_kernel import RemoteKernel
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


@pytest.mark.parametrize('error', [ValueError, OSError])
def test_opensandbox_output_failure_keeps_job_for_cleanup_without_replaying_logs(tmp_path, error):
    pytest.importorskip('opensandbox')
    import base64
    from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
    async def run():
        async def submit(code,run_id,*,timeout):
            session.active_job=run_id
        async def status(op,run_id,offsets):
            return {'job_id':run_id,'done':None,'logs':{
                'stdout':{'data':base64.b64encode(b'too much').decode(),'offset':8,'size':8},
                'stderr':{'data':'','offset':0,'size':0}}}
        async def output(channel, text):
            raise error('quota')
        session = OpenSandboxSession(
            SandboxSettings(api_key='fake',host_workspace_root='/server-only/cache'), workspace_dir=tmp_path)
        session.submit=submit
        session._job_rpc=status
        with pytest.raises(error, match='quota'):
            await asyncio.wait_for(session.execute('print(1)', 'run-test', output), 1)
        assert session.active_job=='run-test'
        assert session._cursor('run-test')['offsets']['stdout']==8
    asyncio.run(run())
