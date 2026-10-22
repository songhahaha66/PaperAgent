"""OpenSandbox SDK adapter; all data-plane calls use its authenticated proxy.

    One forwarded API port is sufficient, including for dynamically created sandboxes.
"""
import asyncio
import time
from dataclasses import dataclass
import json
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import ExecutionOutcome
from .settings import SandboxSettings


@dataclass
class _WorkSandbox:
    sandbox: object = None
    interpreter: object = None




class OpenSandboxSession:
    def __init__(self, settings: SandboxSettings, *, work_id=None, experiment_id=None, workspace_dir=None):
        self.settings = settings
        self.id = None
        self.sandbox = None
        self.interpreter = None
        self.context = None
        self.execution_id = None
        self.work_id = work_id
        self.experiment_id = experiment_id or 'default'
        self.root = '/workspace'
        if workspace_dir is None:
            raise ValueError('持久沙箱需要服务端工作区路径')
        self.workspace_dir = Path(workspace_dir).absolute()
        from config.paths import get_workspaces_path, get_pa_data_base
        from services.file_services.workspace_layout import safe_join, private_root, workspace_path
        from services.file_services.workspace_layout import evidence_root
        base = get_workspaces_path().absolute()
        storage_roots = [base, evidence_root(self.workspace_dir).parent, get_pa_data_base() / 'project-state']
        for index, first in enumerate(storage_roots):
            for second in storage_roots[index+1:]:
                if first.is_relative_to(second) or second.is_relative_to(first):
                    raise ValueError('项目可写目录、后端状态和只读证据目录必须互不嵌套')
        host_roots = [Path(settings.host_workspace_root), Path(settings.host_evidence_root)]
        if any(not value.is_absolute() or '..' in value.parts for value in host_roots):
            raise ValueError('沙箱主机存储根目录必须是明确的绝对路径')
        if host_roots[0].is_relative_to(host_roots[1]) or host_roots[1].is_relative_to(host_roots[0]):
            raise ValueError('沙箱主机文件与证据根目录必须互不嵌套')
        relative = self.workspace_dir.relative_to(base).as_posix()
        if not settings.host_workspace_root or not settings.host_evidence_root:
            raise ValueError('必须配置沙箱服务器持久文件和证据根目录；不会使用临时容器存储')
        self.volume_path = str(safe_join(settings.host_workspace_root, relative))
        archive = workspace_path(self.workspace_dir, 'runs')
        archive.mkdir(parents=True, exist_ok=True)
        self.evidence_path = str(safe_join(settings.host_evidence_root, private_root(self.workspace_dir).name + '/runs'))
        self.host = None
        self.context_restored = False
        from services.file_services.workspace_lock import project_root
        self.lease_path = private_root(project_root(self.workspace_dir)) / 'sandbox-lease.json'
        import uuid
        self.lease_id = uuid.uuid4().hex

    def _save_lease(self, lease):
        from services.file_services.file_helper import atomic_write
        data = json.loads(self.lease_path.read_text()) if self.lease_path.exists() else {}
        leases = data.get('leases', {})
        if data and 'leases' not in data:
            # A prior unconfirmed container cannot be forgotten during upgrade.
            leases['previous'] = data
        leases[self.lease_id] = lease
        data = {'leases': leases, 'expires_at': max(row['expires_at'] for row in leases.values())}
        atomic_write(self.lease_path, lambda f: f.write(json.dumps(data).encode()))

    def _remove_lease(self):
        from services.file_services.file_helper import atomic_write
        data = json.loads(self.lease_path.read_text())
        leases = data['leases']
        leases.pop(self.lease_id, None)
        if not leases:
            self.lease_path.unlink()
        else:
            data['expires_at'] = max(row['expires_at'] for row in leases.values())
            atomic_write(self.lease_path, lambda f: f.write(json.dumps(data).encode()))

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
        import uuid
        from services.file_services.file_helper import atomic_write
        probe = self.workspace_dir / '.volume-probe'
        from services.file_services.workspace_layout import workspace_path
        evidence_probe = workspace_path(self.workspace_dir, 'runs/.volume-probe')
        expected = uuid.uuid4().hex
        atomic_write(probe, lambda f: f.write(expected.encode()))
        atomic_write(evidence_probe, lambda f: f.write(expected.encode()))
        self.host = _WorkSandbox()
        await self._create(config, Sandbox, NetworkPolicy)
        self.id = self.sandbox.id
        mounted = await self.sandbox.files.read_file('/workspace/.volume-probe')
        archived = await self.sandbox.files.read_file('/evidence/.volume-probe')
        if mounted != expected or archived != expected:
            raise RuntimeError('沙箱持久卷与后端共享目录不一致；拒绝执行，不创建文件镜像')
        probe.unlink(missing_ok=True)
        evidence_probe.unlink(missing_ok=True)
        await self.sandbox.files.create_directories([WriteEntry(path=self.root + '/' + p, mode=755)
            for p in ('data', 'attachment', 'code', 'outputs/intermediate')])
        self.context_restored = False
        self.context = await self.interpreter.codes.create_context('python')
        ready = await self.interpreter.codes.run(
            "import sys\nsys.path.insert(0, '/tmp')\nimport paperagent_runtime as _pa_runtime\nfrom paperagent_runtime import emit_result\n_pa_runtime.setup(" + repr(self.root) + ")", context=self.context)
        if ready.error or ready.complete is None:
            raise RuntimeError(self._error(ready.error) if ready.error else "Python 初始化缺少完成回执")

    async def _create(self, config, Sandbox, NetworkPolicy):
        from code_interpreter import CodeInterpreter
        from opensandbox.models import Volume, Host
        from services.file_services.file_helper import atomic_write
        self.lease_path.parent.mkdir(parents=True, exist_ok=True)
        lease = {'expires_at': time.time() + self.settings.lifetime + self.settings.startup_timeout, 'sandbox_id': None}
        self._save_lease(lease)
        self.sandbox = await Sandbox.create(self.settings.image, connection_config=config,
            timeout=timedelta(seconds=self.settings.lifetime),
            ready_timeout=timedelta(seconds=self.settings.startup_timeout),
            entrypoint=["/opt/code-interpreter/code-interpreter.sh"],
            resource={"cpu": self.settings.cpu, "memory": self.settings.memory},
            network_policy=NetworkPolicy(default_action="deny"),
            volumes=[Volume(name='project', host=Host(path=self.volume_path), mount_path='/workspace'),
                     Volume(name='evidence', host=Host(path=self.evidence_path), mount_path='/evidence', read_only=True)],
            metadata={"application": "paperagent"},
            env={"PYTHON_VERSION": "3.11", "MPLBACKEND": "Agg", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"})
        self.host.sandbox = self.sandbox
        lease['sandbox_id'] = self.sandbox.id
        self._save_lease(lease)
        await self.sandbox.files.write_file('/tmp/paperagent_runtime.py', Path(__file__).with_name('runtime_helpers.py').read_bytes())
        self.interpreter = await CodeInterpreter.create(sandbox=self.sandbox)
        self.host.interpreter = self.interpreter

    @staticmethod
    def _error(error):
        text = "\n".join(error.traceback) or f"{error.name}: {error.value}"
        return re.sub(r'\x1b\[[0-9;]*m', '', text)

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

    async def command(self, command, timeout):
        from opensandbox.models import RunCommandOpts
        from opensandbox.models.execd import ExecutionHandlers
        logs = {'stdout': [], 'stderr': []}
        size = 0
        failed = asyncio.get_running_loop().create_future()
        async def emit(channel, message):
            nonlocal size
            raw = message.text.encode('utf-8')
            remaining = max(0, self.settings.max_log_bytes - size)
            size += len(raw)
            logs[channel].append(raw[:remaining].decode('utf-8', errors='ignore'))
            if size > self.settings.max_log_bytes and not failed.done():
                failed.set_result(RuntimeError('编译输出超过日志上限，执行已停止'))
        async def stdout(message): await emit('stdout', message)
        async def stderr(message): await emit('stderr', message)
        async def initialized(message): self.execution_id = message.id
        task = asyncio.create_task(self.sandbox.commands.run(command,
            opts=RunCommandOpts(timeout=timedelta(seconds=timeout), working_directory=self.root),
            handlers=ExecutionHandlers(on_stdout=stdout, on_stderr=stderr, on_init=initialized, skip_accumulation=True)))
        try:
            await asyncio.wait({task, failed}, return_when=asyncio.FIRST_COMPLETED)
            if failed.done():
                if self.execution_id:
                    await self.sandbox.commands.interrupt(self.execution_id)
                raise failed.result()
            result = await task
            if result.exit_code is None or result.error:
                raise RuntimeError('编译缺少成功完成回执：' + (self._error(result.error) if result.error else 'unknown exit code'))
            return result.exit_code, ''.join(logs['stdout']), ''.join(logs['stderr'])
        except BaseException as exc:
            exc.sandbox_stdout = ''.join(logs['stdout'])
            exc.sandbox_stderr = ''.join(logs['stderr'])
            raise
        finally:
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            failed.cancel()
            self.execution_id = None

    async def interrupt(self):
        if self.interpreter and self.execution_id:
            await self.interpreter.codes.interrupt(self.execution_id)

    @staticmethod
    async def _destroy_host(host):
        try:
            if host.sandbox:
                await host.sandbox.destroy()
        finally:
            if host.interpreter:
                await host.interpreter.aclose()
            host.sandbox = host.interpreter = None

    async def release(self):
        await self.close()

    async def close(self):
        host = self.host
        if host:
            try:
                if host.sandbox:
                    await self._destroy_host(host)
                    self._remove_lease()
            finally:
                self.host = None
                self.sandbox = self.interpreter = self.context = None
                self.id = self.execution_id = None
