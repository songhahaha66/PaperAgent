"""OpenSandbox SDK adapter; all data-plane calls use its authenticated proxy.

    One forwarded API port is sufficient, including for dynamically created sandboxes.
"""
import asyncio
import json
import re
import shlex
from datetime import timedelta
from contextlib import aclosing
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import ExecutionOutcome, SandboxFile
from .settings import SandboxSettings


class OpenSandboxSession:
    def __init__(self, settings: SandboxSettings):
        self.settings = settings
        self.id = None
        self.sandbox = None
        self.interpreter = None
        self.context = None
        self.execution_id = None

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
        self.sandbox = await Sandbox.create(self.settings.image, connection_config=config,
            timeout=timedelta(seconds=self.settings.lifetime),
            ready_timeout=timedelta(seconds=self.settings.startup_timeout),
            entrypoint=["/opt/code-interpreter/code-interpreter.sh"],
            resource={"cpu": self.settings.cpu, "memory": self.settings.memory},
            network_policy=NetworkPolicy(default_action="deny"),
            metadata={"application": "paperagent"},
            env={"PYTHON_VERSION": "3.11", "MPLBACKEND": "Agg", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"})
        self.id = self.sandbox.id
        await self.sandbox.files.create_directories([WriteEntry(path="/workspace/" + p, mode=755)
            for p in ("data", "attachment", "code", "outputs/intermediate", "runs")])
        await self.sandbox.files.write_file("/tmp/paperagent_runtime.py", Path(__file__).with_name("runtime_helpers.py").read_bytes())
        self.interpreter = await CodeInterpreter.create(sandbox=self.sandbox)
        self.context = await self.interpreter.codes.create_context("python")
        ready = await self.interpreter.codes.run("import sys\nsys.path.insert(0, '/tmp')\nimport paperagent_runtime as _pa_runtime\n_pa_runtime.setup()", context=self.context)
        if ready.error:
            raise RuntimeError(self._error(ready.error))

    @staticmethod
    def _error(error):
        text = "\n".join(error.traceback) or f"{error.name}: {error.value}"
        return re.sub(r'\x1b\[[0-9;]*m', '', text)

    async def upload(self, path, source):
        from opensandbox.models import WriteEntry
        target = "/workspace/" + path
        await self.sandbox.files.create_directories([WriteEntry(path=str(Path(target).parent), mode=755)])
        with source.open("rb") as stream:
            await self.sandbox.files.write_file(target, stream, mode=644)

    async def delete(self, path):
        await self.sandbox.files.delete_files(["/workspace/" + path])

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
        figures = await self.interpreter.codes.run(f"_pa_runtime.save_figures({run_id!r})", context=self.context)
        error = self._error(result.error) if result.error else None
        if figures.error:
            error = (error or "") + "\n保存图片失败: " + self._error(figures.error)
        return ExecutionOutcome(error=error)

    async def files(self):
        script = "import sys;sys.path.insert(0,'/tmp');from paperagent_runtime import inventory;inventory(%d)" % self.settings.max_file_bytes
        result = await self.sandbox.commands.run("python3 -c " + shlex.quote(script))
        if result.error or result.exit_code not in (None, 0):
            raise RuntimeError("读取沙箱文件清单失败: " + str(result))
        text = "".join(m.text for m in result.logs.stdout)
        return [SandboxFile(**item) for item in json.loads(text)]

    async def download(self, path, destination, max_bytes):
        size = 0
        chunks = await self.sandbox.files.read_bytes_stream("/workspace/" + path)
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

    async def close(self):
        try:
            if self.sandbox:
                await self.sandbox.destroy()
        finally:
            if self.interpreter:
                await self.interpreter.aclose()
            self.sandbox = self.interpreter = self.context = None
            self.id = self.execution_id = None
