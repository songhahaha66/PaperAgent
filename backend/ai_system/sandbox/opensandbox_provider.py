"""OpenSandbox SDK adapter; all data-plane calls use its authenticated proxy.

    One forwarded API port is sufficient, including for dynamically created sandboxes.
"""
import asyncio
import hashlib
import time
from dataclasses import dataclass, field
import json
import re
import shlex
from datetime import timedelta
from contextlib import aclosing
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import ExecutionOutcome, SandboxFile
from .settings import SandboxSettings


@dataclass
class _WorkSandbox:
    sandbox: object = None
    interpreter: object = None
    contexts: dict = field(default_factory=dict)
    expires_at: float = 0
    busy: bool = True
    cleanup: object = None


# Only live leases, never business state. Server TTL is the crash-cleanup backstop.
_WORK_SANDBOXES = {}


class OpenSandboxSession:
    def __init__(self, settings: SandboxSettings, *, work_id=None, experiment_id=None):
        self.settings = settings
        self.id = None
        self.sandbox = None
        self.interpreter = None
        self.context = None
        self.execution_id = None
        self.work_id = work_id
        self.experiment_id = experiment_id or 'default'
        self.root = '/workspace/' + hashlib.sha256(self.experiment_id.encode()).hexdigest()[:24]
        self.host = None
        self.context_restored = False
        # Configuration changes must never borrow a container created with old limits.
        self.cache_key = (work_id, hashlib.sha256((repr(settings) + settings.api_key).encode()).hexdigest())

    async def start(self):
        try:
            from opensandbox import Sandbox
            from opensandbox.config import ConnectionConfig
            from opensandbox.transport import RetryPolicy
            from opensandbox.models import NetworkPolicy, WriteEntry
            from code_interpreter import CodeInterpreter
        except ImportError as exc:
            raise RuntimeError("远端沙箱需要客户端依赖：uv sync --extra sandbox") from exc
        url = urlsplit(self.settings.endpoint)
        config = ConnectionConfig(domain=url.netloc, protocol=url.scheme,
            api_key=self.settings.api_key, use_server_proxy=True,
            retry_policy=RetryPolicy.disabled(),
            request_timeout=timedelta(seconds=max(300, self.settings.startup_timeout)))
        host = _WORK_SANDBOXES.get(self.cache_key) if self.work_id else None
        if host and not host.busy and host.expires_at > time.monotonic() + 180:
            host.busy = True
            if host.cleanup: host.cleanup.cancel()
            self.host = host
            self.sandbox, self.interpreter = host.sandbox, host.interpreter
        else:
            self.host = _WorkSandbox()
            if self.work_id and (not host or not host.busy):
                _WORK_SANDBOXES[self.cache_key] = self.host
                if host:
                    await self._destroy_host(host)
            await self._create(config, Sandbox, NetworkPolicy)
        self.id = self.sandbox.id
        await self.sandbox.files.create_directories([WriteEntry(path=self.root + '/' + p, mode=755)
            for p in ('data', 'attachment', 'code', 'outputs/intermediate', 'runs')])
        self.context = self.host.contexts.get(self.experiment_id)
        self.context_restored = self.context is not None
        if self.context is None:
            self.context = await self.interpreter.codes.create_context('python')
            ready = await self.interpreter.codes.run(
                "import sys\nsys.path.insert(0, '/tmp')\nimport paperagent_runtime as _pa_runtime\nfrom paperagent_runtime import emit_result\n_pa_runtime.setup(" + repr(self.root) + ")", context=self.context)
            if ready.error:
                raise RuntimeError(self._error(ready.error))
            self.host.contexts[self.experiment_id] = self.context

    async def _create(self, config, Sandbox, NetworkPolicy):
        from code_interpreter import CodeInterpreter
        self.sandbox = await Sandbox.create(self.settings.image, connection_config=config,
            timeout=timedelta(seconds=self.settings.lifetime),
            ready_timeout=timedelta(seconds=self.settings.startup_timeout),
            entrypoint=["/opt/code-interpreter/code-interpreter.sh"],
            resource={"cpu": self.settings.cpu, "memory": self.settings.memory},
            network_policy=NetworkPolicy(default_action="deny"),
            metadata={"application": "paperagent"},
            env={"PYTHON_VERSION": "3.11", "MPLBACKEND": "Agg", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"})
        self.host.sandbox = self.sandbox
        self.host.expires_at = time.monotonic() + self.settings.lifetime
        await self.sandbox.files.write_file('/tmp/paperagent_runtime.py', Path(__file__).with_name('runtime_helpers.py').read_bytes())
        self.interpreter = await CodeInterpreter.create(sandbox=self.sandbox)
        self.host.interpreter = self.interpreter

    @staticmethod
    def _error(error):
        text = "\n".join(error.traceback) or f"{error.name}: {error.value}"
        return re.sub(r'\x1b\[[0-9;]*m', '', text)

    async def upload(self, path, source):
        from opensandbox.models import WriteEntry
        target = self.root + "/" + path
        await self.sandbox.files.create_directories([WriteEntry(path=str(Path(target).parent), mode=755)])
        with source.open("rb") as stream:
            await self.sandbox.files.write_file(target, stream, mode=644)

    async def delete(self, path):
        await self.sandbox.files.delete_files([self.root + "/" + path])

    async def execute(self, code, run_id, output):
        from opensandbox.models.execd import ExecutionHandlers
        callback_error = asyncio.get_running_loop().create_future()
        async def emit(channel, text):
            # SDK 1.1.1 logs and swallows ordinary handler exceptions. Race an
            # explicit failure signal so output quotas actually stop execution.
            try:
                await output(channel, text)
            except Exception as exc:
                if not callback_error.done():
                    callback_error.set_result(exc)
        async def stdout(message):
            await emit("stdout", message.text)
        async def stderr(message):
            await emit("stderr", message.text)
        async def initialized(message):
            self.execution_id = message.id
        task = asyncio.create_task(self.interpreter.codes.run(code, context=self.context,
            handlers=ExecutionHandlers(on_stdout=stdout, on_stderr=stderr, on_init=initialized, skip_accumulation=True)))
        try:
            await asyncio.wait({task, callback_error}, return_when=asyncio.FIRST_COMPLETED)
            if callback_error.done():
                raise callback_error.result()
            result = await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            callback_error.cancel()
        if result.complete is None and result.error is None:
            raise RuntimeError("沙箱输出连接已结束，但没有执行完成回执；不会自动重跑代码")
        self.execution_id = None
        for value in result.result:
            if value.text:
                await output("stdout", value.text + "\n")
        return ExecutionOutcome(error=self._error(result.error) if result.error else None)

    @property
    def context_id(self):
        return self.context.id if self.context else None

    async def finalize(self, run_id):
        figures = await self.interpreter.codes.run(f"_pa_runtime.save_figures({run_id!r})", context=self.context)
        if figures.error:
            raise RuntimeError("保存图片失败: " + self._error(figures.error))
        if figures.complete is None:
            raise RuntimeError("保存图片未收到完成回执，请重试文件交付")

    async def files(self):
        script = "import sys;from pathlib import Path;sys.path.insert(0,'/tmp');import paperagent_runtime as r;r.ROOT=Path(%r);r.inventory(%d)" % (self.root, self.settings.max_file_bytes)
        result = await self.sandbox.commands.run("python3 -c " + shlex.quote(script))
        if result.error or result.exit_code not in (None, 0):
            raise RuntimeError("读取沙箱文件清单失败: " + str(result))
        text = "".join(m.text for m in result.logs.stdout)
        return [SandboxFile(**item) for item in json.loads(text)]

    async def download(self, path, destination, max_bytes):
        size = 0
        chunks = await self.sandbox.files.read_bytes_stream(self.root + "/" + path)
        with destination.open("wb") as stream:
            async with aclosing(chunks):
                async for chunk in chunks:
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("文件下载超过大小上限")
                    stream.write(chunk)

    async def interrupt(self):
        if self.interpreter and self.execution_id:
            await self.interpreter.codes.interrupt(self.execution_id)

    @staticmethod
    async def _destroy_host(host):
        if host.cleanup: host.cleanup.cancel()
        try:
            if host.sandbox:
                await host.sandbox.destroy()
        finally:
            if host.interpreter:
                await host.interpreter.aclose()
            host.sandbox = host.interpreter = None
            host.contexts.clear()

    async def release(self):
        host = self.host
        if not host:
            return
        if _WORK_SANDBOXES.get(self.cache_key) is not host:
            await self.close()
            return
        host.busy = False
        key = self.cache_key
        async def expire():
            if _WORK_SANDBOXES.get(key) is host and not host.busy:
                _WORK_SANDBOXES.pop(key, None)
                try:
                    await self._destroy_host(host)
                except Exception:
                    pass  # Server TTL still reclaims the container.
        host.cleanup = asyncio.get_running_loop().call_later(300, lambda: asyncio.create_task(expire()))
        self.host = None
        self.sandbox = self.interpreter = self.context = None
        self.id = self.execution_id = None

    async def close(self):
        host = self.host
        if host:
            if _WORK_SANDBOXES.get(self.cache_key) is host:
                _WORK_SANDBOXES.pop(self.cache_key, None)
            try:
                await self._destroy_host(host)
            finally:
                self.host = None
                self.sandbox = self.interpreter = self.context = None
                self.id = self.execution_id = None
