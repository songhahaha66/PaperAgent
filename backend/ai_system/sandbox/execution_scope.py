"""Serialize runtime access without retaining a lock on user documents."""
import asyncio
from contextlib import asynccontextmanager
from services.file_services.workspace_lock import execution_lock


class KernelExecutionScope:
    def __init__(self, workspace):
        self.workspace = workspace
        self._members = set()
        self._lifecycle = asyncio.Lock()
        self._operation = asyncio.Lock()

    async def retain(self, member, timeout):
        async with asyncio.timeout(timeout):
            async with self._lifecycle:
                if member in self._members:
                    return
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
            session=member.session
            async with asyncio.timeout(timeout):
                lock=execution_lock(self.workspace,cache_key=session.cache_key,
                    allowed_leases=[getattr(session,'lease_id','')])
                await lock.__aenter__()
            try:yield
            finally:await lock.__aexit__(None,None,None)
        finally:
            self._operation.release()

    async def release(self, member):
        async with self._lifecycle:
            self._members.discard(member)
