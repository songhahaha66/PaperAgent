"""Controlled execution on the current, separate-filesystem transfer protocol."""
import asyncio
from helpers.remote_files import RemoteFiles
from ai_system.sandbox.contracts import ExecutionOutcome


class FakeSession(RemoteFiles):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.ready = asyncio.Event()

    async def execute(self, code, run_id, output):
        self.calls.append(code)
        await output('stdout', 'started\n')
        (self.remote / 'outputs/table.csv').write_text(code)
        self.ready.set()
        if code == 'wait':
            await asyncio.sleep(10)
        if code == 'verbose':
            await output('stderr', 'x' * 100)
        if code == 'disconnect':
            raise ConnectionError('connection lost')
        await output('stderr', 'warning\n')
        return ExecutionOutcome('ValueError: test' if code == 'error' else None)
