"""Shared builders extracted from test_sandbox_providers."""
import asyncio
import hashlib
from ai_system.sandbox.contracts import ExecutionOutcome, SandboxFile


class FakeSession:
    id = 'remote-test'

    def __init__(self):
        self.content = {}
        self.started = self.closed = self.interrupted = 0
        self.calls = []
        self.ready = asyncio.Event()

    async def start(self):
        self.started += 1

    async def upload(self, path, source):
        self.content[path] = source.read_bytes()

    async def delete(self, path):
        self.content.pop(path, None)

    async def execute(self, code, run_id, output):
        self.calls.append(code)
        await output('stdout', 'started\n')
        self.content['outputs/table.csv'] = code.encode()
        self.ready.set()
        if code == 'wait':
            await asyncio.sleep(10)
        if code == 'verbose':
            await output('stderr', 'x' * 100)
        if code == 'disconnect':
            raise ConnectionError('connection lost')
        await output('stderr', 'warning\n')
        return ExecutionOutcome('ValueError: test' if code == 'error' else None)

    async def files(self):
        return [SandboxFile(path, len(data), hashlib.sha256(data).hexdigest()) for path, data in self.content.items()]

    async def download(self, path, destination, max_bytes):
        destination.write_bytes(self.content[path])

    async def interrupt(self):
        self.interrupted += 1

    async def close(self):
        self.closed += 1
