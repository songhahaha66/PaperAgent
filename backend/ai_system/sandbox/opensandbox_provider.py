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
from .lifetime import SandboxLifetimeCap, cap_rejected, granted_lifetime, renewal_seconds
from .settings import SandboxSettings


@dataclass
class _WorkSandbox:
    sandbox: object = None
    interpreter: object = None




class OpenSandboxSession:
    def __init__(self, settings: SandboxSettings, *, work_id=None, experiment_id=None, workspace_dir=None, cache_key=None):
        url = urlsplit(settings.endpoint)
        if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.query or url.fragment or url.path not in {'', '/'}:
            raise ValueError('OpenSandbox endpoint 必须是 http(s) 服务地址，不含路径或凭据')
        if url.scheme == 'http' and url.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('远端 OpenSandbox 使用 HTTPS，或通过 SSH 隧道连接 localhost')
        if not settings.api_key:
            raise ValueError('OpenSandbox 缺少 API key')
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
        from services.file_services.workspace_layout import private_root
        import hashlib
        # This path is on the sandbox server only. It is never opened by the backend.
        self.cache_key = cache_key or hashlib.sha256(str(self.workspace_dir).encode()).hexdigest()
        if not re.fullmatch(r'[a-f0-9]{64}', self.cache_key):
            raise ValueError('Invalid server cache identity')
        if not settings.host_workspace_root or not Path(settings.host_workspace_root).is_absolute() or '..' in Path(settings.host_workspace_root).parts:
            raise ValueError('必须配置沙箱服务器本地持久缓存根目录')
        self.volume_path = str(Path(settings.host_workspace_root) / self.cache_key)
        self.host = None
        self.context_restored = False
        from services.file_services.workspace_lock import project_root
        self.lease_path = private_root(project_root(self.workspace_dir)) / 'sandbox-lease.json'
        import uuid
        self.lease_id = uuid.uuid4().hex

    def _save_lease(self, lease):
        from .runtime_manager import LeaseLedger
        LeaseLedger.save(self.lease_path,self.lease_id,{**lease,'cache_key':getattr(self,'cache_key',None)})

    def _remove_lease(self):
        from .runtime_manager import LeaseLedger
        LeaseLedger.remove(self.lease_path,self.lease_id)

    def _config(self):
        from opensandbox.config import ConnectionConfig
        from opensandbox.transport import RetryPolicy
        url=urlsplit(self.settings.endpoint)
        return ConnectionConfig(domain=url.netloc,protocol=url.scheme,api_key=self.settings.api_key,
            use_server_proxy=True,retry_policy=RetryPolicy.disabled(),
            request_timeout=timedelta(seconds=max(300,self.settings.startup_timeout)))

    async def attach(self, handle):
        from opensandbox import Sandbox
        from code_interpreter import CodeInterpreter
        from .runtime_manager import RuntimeGone
        self.lease_id=handle['lease_id']
        self.host=_WorkSandbox()
        try:
            self.sandbox=await Sandbox.connect(handle['sandbox_id'],connection_config=self._config())
            self.host.sandbox=self.sandbox
            self.interpreter=await CodeInterpreter.create(sandbox=self.sandbox)
            self.host.interpreter=self.interpreter
            self.context=await self.interpreter.codes.get_context(handle['context_id'])
        except Exception as exc:
            await self.disconnect()
            if getattr(exc,'status_code',None)==404:
                # A missing context does not prove the container is gone.
                if self.sandbox is not None:raise RuntimeError('原 Python 上下文已失效，请显式重置运行时') from exc
                self._remove_lease()
                raise RuntimeGone('原沙箱已不存在') from exc
            raise
        self.id=self.sandbox.id
        self.expires_at=handle.get('expires_at') or 0
        self.context_restored=True

    async def disconnect(self):
        calls=[]
        if self.interpreter:calls.append(self.interpreter.aclose())
        if self.sandbox:calls.append(self.sandbox.close())
        results=await asyncio.gather(*calls,return_exceptions=True)
        for result in results:
            if isinstance(result,BaseException):raise result

    async def attach_for_destroy(self, handle):
        from opensandbox import Sandbox
        self.lease_id=handle['lease_id']
        try:self.sandbox=await Sandbox.connect(handle['sandbox_id'],connection_config=self._config())
        except Exception as exc:
            if getattr(exc,'status_code',None)!=404:raise
            self._remove_lease()
            return
        self.host=_WorkSandbox(sandbox=self.sandbox)
        self.id=self.sandbox.id

    async def start(self):
        try:
            from opensandbox import Sandbox
            from opensandbox.config import ConnectionConfig
            from opensandbox.transport import RetryPolicy
            from opensandbox.models import NetworkPolicy, WriteEntry
            from code_interpreter import CodeInterpreter
        except ImportError as exc:
            raise RuntimeError("远端沙箱需要客户端依赖：uv sync --extra sandbox") from exc
        config = self._config()
        self.host = _WorkSandbox()
        await self._create(config, Sandbox, NetworkPolicy)
        self.id = self.sandbox.id
        await self.sandbox.files.create_directories([WriteEntry(path=self.root + '/' + p, mode=755)
            for p in ('data', 'attachment', 'code', 'outputs/intermediate', '.build/latex')])
        self.context_restored = False
        self.context = await self.interpreter.codes.create_context('python')

    async def _create(self, config, Sandbox, NetworkPolicy):
        from code_interpreter import CodeInterpreter
        from opensandbox.models import Volume, Host
        self.lease_path.parent.mkdir(parents=True, exist_ok=True)
        # The server starts the TTL when it accepts create. Do not add startup_timeout:
        # that made the local clock later than the real recycle time.
        grant = granted_lifetime(self.settings)
        self.expires_at = time.time() + grant
        lease = {'expires_at': self.expires_at, 'sandbox_id': None}
        self._save_lease(lease)
        self.sandbox = await Sandbox.create(self.settings.image, connection_config=config,
            timeout=timedelta(seconds=grant),
            ready_timeout=timedelta(seconds=self.settings.startup_timeout),
            entrypoint=["/opt/code-interpreter/code-interpreter.sh"],
            resource={"cpu": self.settings.cpu, "memory": self.settings.memory},
            network_policy=NetworkPolicy(default_action="deny"),
            volumes=[Volume(name='execution-cache', host=Host(path=self.volume_path), mount_path='/workspace')],
            metadata={"application": "paperagent"},
            env={"PYTHON_VERSION": "3.11", "MPLBACKEND": "Agg", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
        self.host.sandbox = self.sandbox
        lease['sandbox_id'] = self.sandbox.id
        self._save_lease(lease)
        if callback:=getattr(self,'on_created',None):callback(self.sandbox.id)
        if callback:=getattr(self,'on_expiry',None):callback(self.expires_at)
        await self.sandbox.files.write_file('/tmp/paperagent_runtime.py', Path(__file__).with_name('runtime_helpers.py').read_bytes())
        await self.sandbox.files.write_file('/tmp/paperagent_transfer.py', Path(__file__).with_name('transfer_worker.py').read_bytes())
        self.interpreter = await CodeInterpreter.create(sandbox=self.sandbox)
        self.host.interpreter = self.interpreter

    @staticmethod
    def _error(error):
        text = "\n".join(error.traceback) or f"{error.name}: {error.value}"
        return re.sub(r'\x1b\[[0-9;]*m', '', text)

    @staticmethod
    def _relative(value, *, snapshot=False):
        from services.file_services.workspace_layout import relative_parts, PRIVATE_NAMES
        parts = relative_parts(value)
        if snapshot:
            if len(parts)<4 or parts[0]!='.pa-runs' or parts[2]!='files':
                raise ValueError('Invalid frozen-output path')
        elif any(part in PRIVATE_NAMES or part.startswith('.pa-') for part in parts):
            raise ValueError('Private cache path')
        return '/'.join(parts)

    async def _rpc(self, op, **values):
        import shlex
        request = {'op':op,'limit':self.settings.max_file_bytes,**values}
        code, out, err = await self.command('python3 /tmp/paperagent_transfer.py ' + shlex.quote(json.dumps(request)),60)
        if code!=0: raise RuntimeError('沙箱文件操作失败：'+err[:1000])
        return json.loads(out)

    async def inventory(self, paths=None, *, kind='python'):
        if paths is not None: paths=[self._relative(p) for p in paths]
        return await self._rpc('inventory',paths=paths,kind=kind)

    async def upload(self, path, source, sha256):
        from opensandbox.models import WriteEntry
        import uuid
        path=self._relative(path); temporary='.pa-upload-'+uuid.uuid4().hex
        await self.sandbox.files.create_directories([WriteEntry(path=self.root,mode=755)])
        from services.file_services.file_helper import open_read
        try:
            with open_read(source) as stream:
                await self.sandbox.files.write_file(self.root+'/'+temporary,stream,mode=600)
            await self._rpc('install',path=path,temporary=temporary,sha256=sha256)
        finally:
            await self.sandbox.files.delete_files([self.root+'/'+temporary])

    async def input_versions(self):
        data=await self._rpc('inputs')
        return {self._relative(p):h for p,h in data.items()}

    async def save_input_versions(self, versions):
        import uuid
        temporary='.pa-ledger-'+uuid.uuid4().hex
        try:
            await self.sandbox.files.write_file(self.root+'/'+temporary,json.dumps(versions),mode=600)
            await self._rpc('commit_inputs',temporary=temporary)
        finally:
            await self.sandbox.files.delete_files([self.root+'/'+temporary])

    async def delete_input(self, path):
        await self._rpc('delete',path=self._relative(path))

    async def capture(self, run_id, baseline, *, kind='python'):
        return await self._rpc('freeze',run_id=run_id,baseline=baseline,kind=kind)

    async def capture_inputs(self, run_id, baseline, paths):
        return await self._rpc('freeze',run_id=run_id,baseline=baseline,kind='inputs',paths=[self._relative(p) for p in paths])

    async def frozen(self, run_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',run_id):raise ValueError('Invalid run ID')
        from contextlib import aclosing
        chunks=await self.sandbox.files.read_bytes_stream(self.root+'/.pa-runs/'+run_id+'/manifest.json')
        data=bytearray()
        async with aclosing(chunks):
            async for chunk in chunks:
                if len(data)+len(chunk)>self.settings.max_log_bytes:raise ValueError('Output manifest too large')
                data.extend(chunk)
        return json.loads(data)

    async def download(self, snapshot, destination, max_bytes):
        from contextlib import aclosing
        snapshot=self._relative(snapshot,snapshot=True)
        chunks=await self.sandbox.files.read_bytes_stream(self.root+'/'+snapshot)
        size=0
        with destination.open('wb') as target:
            async with aclosing(chunks):
                async for chunk in chunks:
                    size+=len(chunk)
                    if size>max_bytes:raise ValueError('下载超过单文件上限')
                    target.write(chunk)

    async def renew(self, seconds):
        """Slide expiration to now + seconds, never beyond one server grant."""
        seconds = int(seconds)
        if seconds > self.settings.max_timeout:
            raise SandboxLifetimeCap(f'单次沙箱寿命不能超过 {self.settings.max_timeout} 秒')
        if seconds < 1:
            raise SandboxLifetimeCap('续期时长无效')
        try:
            response = await self.sandbox.renew(timedelta(seconds=seconds))
        except SandboxLifetimeCap:
            raise
        except Exception as exc:
            if cap_rejected(exc):
                raise SandboxLifetimeCap('服务器拒绝超过单次寿命上限的续期') from exc
            raise
        expires = getattr(response, 'expires_at', None)
        epoch = expires.timestamp() if hasattr(expires, 'timestamp') else time.time() + seconds
        self.expires_at = float(epoch)
        if self.id:
            self._save_lease({'expires_at': self.expires_at, 'sandbox_id': self.id})
        if callback := getattr(self, 'on_expiry', None):
            callback(self.expires_at)
        return self.expires_at

    async def extend(self, needed):
        expires = float(getattr(self, 'expires_at', 0) or 0)
        if expires - time.time() >= float(needed) + 30:
            return expires
        return await self.renew(renewal_seconds(self.settings, needed))

    async def _sustain(self, needed):
        """Keep renewing a live call so the server TTL cannot cut it off."""
        try:
            while True:
                if not getattr(self, 'sandbox', None) or not hasattr(self.sandbox, 'renew'):
                    return
                expires = float(getattr(self, 'expires_at', 0) or 0)
                remaining = expires - time.time()
                lead = min(120.0, max(30.0, float(needed)))
                if remaining <= lead:
                    try:
                        await self.renew(renewal_seconds(self.settings, needed))
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        return
                else:
                    await asyncio.sleep(min(30.0, max(0.5, remaining - lead)))
        except asyncio.CancelledError:
            return

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
        # A second code request for initialization/figure flushing can race the
        # interpreter's busy state after the previous SSE completion event.
        cell = ("import sys\nsys.path.insert(0, '/tmp')\nimport paperagent_runtime as _pa_runtime\n"
                f"_pa_runtime.run_cell({code!r}, {run_id!r}, globals(), {self.root!r})")
        holder = asyncio.create_task(self._sustain(self.settings.max_timeout))
        task = asyncio.create_task(self.interpreter.codes.run(cell, context=self.context,
            handlers=ExecutionHandlers(on_stdout=stdout, on_stderr=stderr, on_init=initialized, skip_accumulation=True)))
        try:
            await asyncio.wait({task, callback_error}, return_when=asyncio.FIRST_COMPLETED)
            if callback_error.done():
                raise callback_error.result()
            result = await task
        finally:
            holder.cancel()
            await asyncio.gather(holder, return_exceptions=True)
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
        # The execution receipt already covers figure flushing in run_cell.
        return None

    async def command(self, command, timeout):
        from opensandbox.models.execd import RunCommandOpts, ExecutionHandlers
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
        holder = asyncio.create_task(self._sustain(max(timeout, self.settings.max_timeout)))
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
            if result.exit_code is None or (result.error and result.exit_code == 0):
                raise RuntimeError('编译缺少成功完成回执：' + (self._error(result.error) if result.error else 'unknown exit code'))
            return result.exit_code, ''.join(logs['stdout']), ''.join(logs['stderr'])
        except BaseException as exc:
            exc.sandbox_stdout = ''.join(logs['stdout'])
            exc.sandbox_stderr = ''.join(logs['stderr'])
            raise
        finally:
            holder.cancel()
            await asyncio.gather(holder, return_exceptions=True)
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            failed.cancel()
            self.execution_id = None

    async def exec(self, argv, *, stdin=None, timeout=180):
        """Translate literal arguments to the shell API of the pinned SDK."""
        import base64
        import shlex
        if isinstance(argv, (str, bytes)) or not argv or not all(isinstance(arg, str) for arg in argv):
            raise ValueError('沙箱命令必须是非空字符串参数列表')
        command = shlex.join(argv)
        if stdin is not None:
            encoded = base64.b64encode(stdin.encode('utf-8')).decode('ascii')
            command = 'printf %s ' + shlex.quote(encoded) + ' | base64 -d | ' + command
        return await self.command(command, timeout)

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
