"""Run bookkeeping, bounded logs and artifact transfer shared by remote providers."""
import asyncio
import hashlib
import logging
import shutil
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
        self._known = {}
        self._uploaded = {}
        self._lock = asyncio.Lock()
        self.pending_run = next((rid for rid, row in reversed(self.fs._manifest.get('runs', {}).items())
                                 if row.get('delivery_error')), None)

    async def _start(self):
        if self.pending_run:
            if not self._started:
                await self.session.start()
                self._started = True
            info = self.fs.get_run_info(self.pending_run)
            if not info.get('sandbox_id') or info['sandbox_id'] != self.session.id:
                raise RuntimeError('原沙箱已不可用，不能确认待交付文件；已有结果保留，不自动重跑。')
            if info.get('sandbox_root') != getattr(self.session, 'root', None):
                raise RuntimeError('原实验文件目录已不可用，不能确认待交付文件；已有结果保留，不自动重跑。')
            if info.get('finalize_pending') and info.get('context_id') != getattr(self.session, 'context_id', None):
                raise RuntimeError('原执行上下文已不可用，无法恢复图片保存；已有结果保留，不自动重跑。')
            if info.get('pending_files') is None and 'output_baseline' not in info:
                raise RuntimeError('旧运行缺少文件清单和执行前记录，无法确认交付完整性；不自动重跑。')
            self._known = dict(info.get('output_baseline', {}))
            return  # Never upload old local outputs over undelivered remote files.
        if self._started:
            await self._upload_changes()
            return
        await self.session.start()
        await self._upload_changes()
        self._known.update({file.path: file.sha256 for file in await self.session.files()})
        self._started = True

    async def _upload_changes(self):
        total = 0
        current = set()
        paths = [(folder, source) for folder in ('data','attachment','code','outputs','runs')
                 for source in sorted((self.workspace_dir / folder).rglob('*'))]
        paths += [('generated', self.fs.abs(path)) for path, meta in self.fs._manifest.get('files', {}).items()
                  if meta.get('source_run') and Path(path).parts[0] not in {'data','attachment','code','outputs','runs'}]
        for folder, source in paths:
            if source.exists():
                if source.is_symlink() or not source.resolve().is_relative_to(self.workspace_dir):
                    raise ValueError("沙箱输入不允许符号链接")
                if not source.is_file() or "__pycache__" in source.parts:
                    continue
                rel = source.relative_to(self.workspace_dir).as_posix()
                # Runtime bookkeeping stays local; inherited artifacts can be reused.
                if folder == "runs" and "/artifacts/" not in rel:
                    continue
                size = source.stat().st_size
                total += size
                if size > self.settings.max_file_bytes or total > self.settings.max_transfer_bytes:
                    raise ValueError("沙箱输入超出传输限额")
                current.add(rel)
                if len(current) > 1000:
                    raise ValueError("沙箱输入文件数超过 1000")
                with source.open('rb') as stream:
                    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
                if self._uploaded.get(rel) != checksum:
                    await self.session.upload(rel, source)
                    self._uploaded[rel] = checksum
                    self._known[rel] = checksum
        for rel in set(self._uploaded) - current:
            await self.session.delete(rel)
            self._uploaded.pop(rel)
            self._known.pop(rel, None)

    async def _collect(self, run_id):
        info = self.fs._manifest['runs'][run_id]
        expected = info.get('pending_files')
        files = await self.session.files()
        if len(files) > 1000:
            raise ValueError("沙箱输出文件数超过 1000")
        changed = []
        total = 0
        for file in files:
            path = safe_path(file.path)
            if path.startswith(('data/', 'attachment/')) or (path.startswith('runs/') and not path.startswith(f'runs/{run_id}/artifacts/')):
                continue
            if expected is not None and path not in expected:
                continue
            if expected is None and self._known.get(path) == file.sha256:
                continue
            total += file.size
            if file.size < 0 or file.size > self.settings.max_file_bytes or total > self.settings.max_transfer_bytes:
                raise ValueError("沙箱输出超出传输限额")
            changed.append((path, file))
        available = {f.path: f.sha256 for f in files}
        if any(available.get(path) != checksum for path, checksum in (expected or {}).items()):
            raise RuntimeError('待同步的远端文件版本已不可用；已保存文件仍保留，请说明交付缺口，不自动重跑。')
        info['pending_files'] = {path: file.sha256 for path,file in changed}
        self.fs._save_manifest()
        saved = []
        for path, file in changed:
            dest = self.fs.abs(path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            temporary = dest.with_name(dest.name + ".download")
            try:
                await self.session.download(path, temporary, self.settings.max_file_bytes)
                with temporary.open("rb") as stream:
                    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                if temporary.stat().st_size != file.size or checksum != file.sha256:
                    raise ValueError(f"输出文件在传输中改变: {path}")
                temporary.replace(dest)
            finally:
                temporary.unlink(missing_ok=True)
            # Preserve each execution's version even when later cells overwrite outputs.
            prefix = f"runs/{run_id}/artifacts/"
            archive = path if path.startswith(prefix) else prefix + path
            if archive != path:
                target = self.fs.abs(archive)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dest, target)
            self.fs.register_run_artifact(run_id, archive.removeprefix(prefix))
            kind = "image" if dest.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg"} else "code" if path.startswith("code/") else "data"
            self.fs.register_file(path, kind=kind, source_run=run_id, visibility="user")
            self._known[path] = file.sha256
            self._uploaded[path] = file.sha256
            saved.append(path)
            info['pending_files'].pop(path, None)
            info.setdefault('output_baseline', {})[path] = file.sha256
            self.fs._save_manifest()
        info['pending_files'] = {}
        self.fs._save_manifest()
        prefix = f'runs/{run_id}/artifacts/'
        return list(dict.fromkeys([*saved, *(p for p in self.fs._manifest['runs'][run_id].get('artifacts', []) if p.startswith(prefix))]))

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
            self._known = {}
            self._uploaded = {}

    async def execute(self, code, *, on_event=None):
        async with self._lock:
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
                await asyncio.wait_for(self._start(), self.settings.startup_timeout)
                info.update(status="running", sandbox_id=self.session.id,
                            sandbox_root=getattr(self.session, 'root', None),
                            context_id=getattr(self.session, 'context_id', None),
                            output_baseline=dict(self._known), pending_files=None)
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
                await event("finished", status=status, error=result.error, files=result.files,
                            delivery_error=result.delivery_error)
            return result

    def _safe_error(self, error):
        text = str(error)
        return text.replace(self.settings.api_key, '[redacted]') if self.settings.api_key else text

    async def retry_outputs(self):
        async with self._lock:
            if not self.pending_run:
                return None
            run_id = self.pending_run
            await asyncio.wait_for(self._start(), self.settings.startup_timeout)
            info = self.fs._manifest['runs'][run_id]
            if info.get('finalize_pending') and info.get('pending_files') is not None:
                # Finish the frozen inventory before discovering new finalized files.
                # A successful figure retry must not bless a changed CSV version.
                await asyncio.wait_for(self._collect(run_id), 60)
            await self._finalize(run_id)
            files = await asyncio.wait_for(self._collect(run_id), 60)
            info = self.fs._manifest['runs'][run_id]
            info.update(files=list(dict.fromkeys([*info.get('files', []), *files])), delivery_error=None)
            self.fs._save_manifest()
            self.pending_run = None
            return run_id

    async def close(self):
        async with self._lock:
            if self._started and hasattr(self.session, 'release'):
                await self.session.release()
                self._started = False
            else:
                await self._stop()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
