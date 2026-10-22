"""Persistent-volume execution, bounded logs and protected run receipts."""
import asyncio
import logging
from pathlib import Path, PurePosixPath

from services.file_services.workspace_fs import WorkspaceFS
from .contracts import SandboxSession
from .kernel import CellResult
from .settings import SandboxSettings

logger = logging.getLogger(__name__)


def safe_path(path: str) -> str:
    p = PurePosixPath(path)
    if p.is_absolute() or not p.parts or ".." in p.parts or "\\" in path or any(part.startswith('.') for part in p.parts) or p.parts[0] in {'manifest.json', 'chat_history.json', 'chat_history.json.lock'}:
        raise ValueError(f"非法沙箱文件路径: {path}")
    return p.as_posix()


class RemoteKernel:
    def __init__(self, workspace_dir, session: SandboxSession, settings: SandboxSettings, timeout=180, fs=None):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.fs = fs or WorkspaceFS(str(self.workspace_dir))
        self.fs.init_structure()
        self.timeout = timeout
        self.session = session
        self.settings = settings
        self._started = False
        self._lock = asyncio.Lock()
        self._project_lock = None
        self.pending_run = next((rid for rid, row in reversed(self.fs._manifest.get('runs', {}).items())
                                 if row.get('delivery_error') and rid not in self.fs._manifest.get('inherited_runs', [])), None)

    async def _start(self):
        if self._started:
            return
        await self.session.start()
        self._started = True
        self.fs._manifest['storage'] = {'kind': 'persistent-host-volume',
            'volume_path': self.session.volume_path, 'sandbox_root': self.session.root}
        self.fs._save_manifest()

    def _freeze_inputs(self, run_id):
        from .evidence import freeze_inputs
        freeze_inputs(self.fs, run_id)

    async def _collect(self, run_id):
        from .artifacts import capture_local_outputs
        info = self.fs._manifest['runs'][run_id]
        from .artifacts import output_hashes
        current = output_hashes(self.fs)
        if info.get('pending_output_hashes') is None:
            info['pending_output_hashes'] = {path: checksum for path, checksum in current.items()
                if info['output_baseline'].get(path) != checksum}
            self.fs._save_manifest()
        expected = info['pending_output_hashes']
        if any(current.get(path) != checksum for path, checksum in expected.items()):
            raise RuntimeError('原运行待归档产物版本已改变或丢失，不能登记新版本；不会重算')
        capture_local_outputs(self.fs, run_id, info['output_baseline'], expected=expected)
        return list(info['artifacts'])

    async def _finalize(self, run_id):
        info = self.fs._manifest['runs'][run_id]
        if info.get('finalize_pending'):
            await asyncio.wait_for(self.session.finalize(run_id), 60)
            info.update(finalize_pending=False, pending_files=None)
            self.fs._save_manifest()

    async def _stop(self):
        try:
            await asyncio.wait_for(self.session.close(), 20)
        finally:
            self._started = False

    async def execute(self, code, *, on_event=None):
        from services.file_services.workspace_lock import execution_lock
        async with self._lock:
            if self._project_lock is None:
                self._project_lock = execution_lock(self.workspace_dir)
                try:
                    await self._project_lock.__aenter__()
                except BaseException:
                    self._project_lock = None
                    raise
            if self.pending_run:
                return CellResult(ok=False, stdout='', error='上次计算的文件尚未取回，请先调用 retry_outputs，不要重算。',
                    run_id=self.pending_run, failure_kind='delivery')
            run_id = self.fs.create_run()
            self.fs.save_run_input(run_id, code)
            self.fs.save_run_stdout(run_id, "")
            self.fs.save_run_stderr(run_id, "")
            info = self.fs._manifest["runs"][run_id]
            info.update(status="starting", provider=self.settings.provider, runtime_image=self.settings.image)
            self.fs._save_manifest()
            result = CellResult(ok=False, stdout="", error=None, run_id=run_id)
            sequence = 0
            size = 0
            async def event(kind, **payload):
                nonlocal sequence
                sequence += 1
                if on_event:
                    try:
                        await on_event({"type": kind, "run_id": run_id, "seq": sequence, **payload})
                    except Exception:
                        logger.warning("沙箱输出推送失败，日志已保存", exc_info=True)
            async def output(channel, text):
                nonlocal size
                if channel not in {"stdout", "stderr"}:
                    raise ValueError("未知沙箱输出通道")
                raw = text.encode("utf-8")
                remaining = max(0, self.settings.max_log_bytes - size)
                size += len(raw)
                text = raw[:remaining].decode("utf-8", errors="ignore")
                with (self.fs.run_dir(run_id) / f"{channel}.log").open("a", encoding="utf-8") as stream:
                    stream.write(text)
                if text:
                    await event("output", channel=channel, text=text)
                if size > self.settings.max_log_bytes:
                    raise RuntimeError("执行输出超过日志限额，已停止；已收到的日志仍保留")
            abnormal = False
            cancelled = False
            execution_finished = False
            await event("starting")
            try:
                from .artifacts import output_hashes
                info['output_baseline'] = output_hashes(self.fs)
                self._freeze_inputs(run_id)
                await asyncio.wait_for(self._start(), self.settings.startup_timeout)
                info.update(status="running", sandbox_id=self.session.id,
                            sandbox_root=getattr(self.session, 'root', None),
                            context_id=getattr(self.session, 'context_id', None),
                            pending_files=None)
                self.fs._save_manifest()
                await event("running")
                outcome = await asyncio.wait_for(self.session.execute(code, run_id, output), self.timeout)
                result.error = outcome.error
                result.ok = result.error is None
                result.failure_kind = 'code' if result.error else None
                execution_finished = True
                # Commit the execution receipt before any fallible output preparation.
                info.update(status='success' if result.ok else 'error', error=result.error,
                            failure_kind=result.failure_kind, finalize_pending=hasattr(self.session, 'finalize'),
                            delivery_error='计算已结束，文件交付尚未完成')
                self.fs._save_manifest()
                try:
                    await self._finalize(run_id)
                except Exception as exc:
                    result.delivery_error = self._safe_error(exc)
                for attempt in range(2):
                    try:
                        result.files = await asyncio.wait_for(self._collect(run_id), 60)
                        break
                    except Exception as exc:
                        if attempt:
                            result.delivery_error = '\n'.join(filter(None, [result.delivery_error, self._safe_error(exc)]))
                if result.delivery_error:
                    self.pending_run = run_id
            except asyncio.CancelledError:
                if execution_finished:
                    result.delivery_error = '文件交付已中断，请调用 retry_outputs，不要重算。'
                    self.pending_run = run_id
                else:
                    cancelled = abnormal = True
                    result.error = "执行已取消"
                    result.failure_kind = 'cancelled'
                raise
            except asyncio.TimeoutError:
                abnormal = True
                result.timed_out = True
                result.failure_kind = 'timeout'
                result.error = "沙箱启动或执行超时，内存变量已失效"
            except Exception as exc:
                abnormal = True
                # Provider failures can contain connection headers. Never echo API keys.
                result.error = self._safe_error(exc)
                result.failure_kind = 'transport_unknown'
            finally:
                if abnormal:
                    try:
                        await asyncio.wait_for(self.session.interrupt(), 5)
                        if self._started:
                            result.files = await asyncio.wait_for(self._collect(run_id), 10)
                    except Exception:
                        logger.warning("中断后未能完整取回文件，已保存日志仍可用")
                    try:
                        await self._stop()
                    except Exception:
                        result.error = (result.error or "") + "；远端清理未确认，将由沙箱 TTL 回收"
                prefix = f"runs/{run_id}/artifacts/"
                result.figures = [p if p.startswith(prefix) else prefix + p
                                  for p in result.files if p.lower().endswith((".png", ".jpg", ".jpeg"))]
                # Only the model observation is abbreviated; disk logs retain received output.
                log = (self.fs.run_dir(run_id) / "stdout.log").read_text(encoding="utf-8")
                stderr = (self.fs.run_dir(run_id) / "stderr.log").read_text(encoding="utf-8")
                combined = log + ("\n" + stderr if stderr else "")
                result.stdout = combined if len(combined) <= 20000 else combined[:10000] + "\n[输出省略；完整内容见运行日志]\n" + combined[-10000:]
                if result.error:
                    with (self.fs.run_dir(run_id) / "stderr.log").open("a", encoding="utf-8") as stream:
                        stream.write("\n" + result.error)
                status = "cancelled" if cancelled else "timeout" if result.timed_out else "success" if result.ok else "error"
                info.update(error=result.error, files=result.files, delivery_error=result.delivery_error,
                            failure_kind=result.failure_kind, context_restored=getattr(self.session, 'context_restored', False))
                self.fs.finish_run(run_id, status)
                if execution_finished and not result.delivery_error:
                    try:
                        from .evidence import seal_run
                        seal_run(self.fs, run_id)
                    except Exception as exc:
                        result.delivery_error = self._safe_error(exc)
                        info['delivery_error'] = result.delivery_error
                        self.fs._save_manifest()
                        self.pending_run = run_id
                await event("finished", status=status, error=result.error, files=result.files,
                            delivery_error=result.delivery_error)
            return result

    def _safe_error(self, error):
        text = str(error)
        return text.replace(self.settings.api_key, '[redacted]') if self.settings.api_key else text

    async def retry_outputs(self):
        from services.file_services.workspace_lock import execution_lock
        from .evidence import seal_run
        async with self._lock:
            if self._project_lock is None:
                self._project_lock = execution_lock(self.workspace_dir)
                try:
                    await self._project_lock.__aenter__()
                except BaseException:
                    self._project_lock = None
                    raise
            if not self.pending_run:
                return None
            run_id = self.pending_run
            info = self.fs.get_run_info(run_id)
            if not self._started and info.get('pending_output_hashes') is None:
                raise RuntimeError('原执行未保存产物版本清单，重启后不能确认归档完整性；保留真实缺口，不重跑')
            # Persistent files survive containers. In-memory figures do not.
            if info.get('finalize_pending'):
                if not self._started or info.get('context_id') != getattr(self.session, 'context_id', None):
                    raise RuntimeError('原 Python 内存已失效，无法确认尚未保存的图；保留真实未完成状态，不重跑')
                await self._finalize(run_id)
            info['files'] = await self._collect(run_id)
            seal_run(self.fs, run_id)
            info['delivery_error'] = None
            self.fs._save_manifest()
            self.pending_run = None
            return run_id

    async def close(self):
        async with self._lock:
            try:
                await self._stop()
            finally:
                if self._project_lock is not None:
                    await self._project_lock.__aexit__(None, None, None)
                    self._project_lock = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
