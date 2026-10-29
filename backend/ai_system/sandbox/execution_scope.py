"""One pool owns the project lock; its independent kernels take turns executing.

The scope never shares Python memory or resource limits between candidates. It
only shares ownership of the OS lock until the last retained kernel is closed.
"""
import asyncio
from contextlib import asynccontextmanager
from services.file_services.workspace_lock import execution_lock


class KernelExecutionScope:
    def __init__(self, workspace):
        self.workspace = workspace
        self._members = set()
        self._lifecycle = asyncio.Lock()
        self._operation = asyncio.Lock()
        self._project_lock = None

    async def retain(self, member, timeout):
        async with asyncio.timeout(timeout):
            async with self._lifecycle:
                if member in self._members:
                    return
                if not self._members:
                    lock = execution_lock(self.workspace)
                    await lock.__aenter__()
                    self._project_lock = lock
                self._members.add(member)

    @asynccontextmanager
    async def operation(self, member, timeout):
        try:
            async with asyncio.timeout(timeout):
                await self.retain(member, timeout)
                await self._operation.acquire()
        except TimeoutError as exc:
            raise TimeoutError('等待同项目计算超时；代码尚未提交，不自动重跑') from exc
        try:
            yield
        finally:
            self._operation.release()

    async def release(self, member):
        async with self._lifecycle:
            self._members.discard(member)
            if not self._members and self._project_lock is not None:
                lock, self._project_lock = self._project_lock, None
                await lock.__aexit__(None, None, None)
