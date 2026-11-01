"""Persistent-volume execution, bounded logs and protected run receipts."""
import asyncio
import logging
import json
from pathlib import Path

from services.file_services.workspace_fs import WorkspaceFS
from .contracts import SandboxSession
from .kernel import CellResult
from .settings import SandboxSettings

logger = logging.getLogger(__name__)


class RemoteKernel:
    def __init__(self, workspace_dir, session: SandboxSession, settings: SandboxSettings, timeout=180, fs=None, execution_scope=None):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.fs = fs or WorkspaceFS(str(self.workspace_dir))
        self.fs.init_structure()
        self.timeout = timeout
        self.session = session
        self.settings = settings
        from .runtime_manager import RuntimeManager
        self.runtime = RuntimeManager(self.workspace_dir,session,settings)
        self._started = False
        from .transfer import WorkspaceTransfer
        self.transfer = WorkspaceTransfer(self.fs,session,settings)
        self._lock = asyncio.Lock()
        from .execution_scope import KernelExecutionScope
        self.execution_scope = execution_scope or KernelExecutionScope(self.workspace_dir)
        self._lifetime_note = None
        import hashlib
        self.owner=hashlib.sha256((str(self.workspace_dir)+'\0'+getattr(session,'experiment_id','default')).encode()).hexdigest()
        self.pending_run = next((rid for rid, row in reversed(self.fs._manifest.get('runs', {}).items())
                                 if (row.get('delivery_error') or (row.get('execution_mode') == 'process'
                                     and (row.get('submission_state') in {'submitting', 'unknown'} or row.get('status') in {'starting', 'running'})))
                                 and (row.get('runtime_owner')==self.owner or row.get('cache_key')==session.cache_key)
                                 and rid not in self.fs._manifest.get('inherited_runs', [])), None)

    def _save_storage(self):
        self.fs._manifest['storage'] = {'kind': 'api-execution-cache',
            'cache_key': self.session.cache_key, 'sandbox_root': self.session.root}
        self.fs._save_manifest()

    async def _abandon_for_replacement(self):
        self._started = False
        self.transfer.seen = None
        await self.runtime.abandon()

    async def _rebuild(self):
        await self._abandon_for_replacement()
        await self.runtime.start()
        self._started = True
        self._save_storage()

    async def _start(self, needed=None):
        from .lifetime import REPLACED_NOTE
        needed = self.timeout if needed is None else needed
        if not self._started:
            await self.runtime.start()
            self._started = True
            self._save_storage()
        # One replacement per call. A sandbox created moments ago is kept even
        # when its grant is shorter than the call; rebuilding it would loop.
        if await self.runtime.prepare(needed) == 'replace':
            await self._abandon_for_replacement()
            if self._lifetime_note != '计算沙箱已到期/已断开，已自动重建并重试':
                self._lifetime_note = REPLACED_NOTE
            await self.runtime.start()
            self._started = True
            self._save_storage()

    def _freeze_inputs(self, run_id):
        from .evidence import freeze_inputs
        freeze_inputs(self.fs, run_id, max_file_bytes=self.settings.max_file_bytes)

    async def _collect(self, run_id, *, recover=False):
        info=self.fs.get_run_info(run_id)
        if info.get('finalize_pending'):raise RuntimeError('图像尚未保存，不能冻结完整产物清单')
        manifest=info.get('output_manifest')
        if manifest is None:
            manifest=await self.transfer.obtain_manifest(run_id,info['output_baseline'],recover=recover)
            info['output_manifest']=manifest
            self.fs._save_manifest()
        return await self.transfer.deliver(run_id,manifest,info['local_output_baseline'])

    async def _finalize(self, run_id):
        info = self.fs._manifest['runs'][run_id]
        if info.get('finalize_pending'):
            await asyncio.wait_for(self.session.finalize(run_id), 60)
            info.update(finalize_pending=False, pending_files=None)
            self.fs._save_manifest()

    async def _stop(self):
        if self.pending_run and getattr(self.session, 'execution_mode', None) == 'process':
            raise RuntimeError('原任务日志或文件尚未取全；请先 retry_outputs，暂不销毁沙箱')
        try:
            await asyncio.wait_for(self.runtime.destroy(), 20)
        finally:
            self._started = False
            self.transfer.seen=None

    def _record_outcome(self, run_id, outcome, result):
        """Commit the on-disk process receipt before any fallible file delivery."""
        result.error = self._safe_error(outcome.error) if outcome.error else None
        result.ok = result.error is None
        result.timed_out = outcome.stop_reason == 'timeout'
        result.failure_kind = ('timeout' if result.timed_out else 'cancelled' if outcome.stop_reason == 'cancelled'
                               else 'resource' if outcome.stop_reason == 'output_limit' else 'code' if result.error else None)
        result.exit_code = outcome.exit_code
        result.duration_seconds = outcome.duration_seconds
        info = self.fs.get_run_info(run_id)
        info.update(submission_state='acknowledged', compute_state='timed_out' if result.timed_out else
                    'cancelled' if outcome.stop_reason == 'cancelled' else 'code_error' if result.error else 'succeeded',
                    exit_code=outcome.exit_code, duration_seconds=outcome.duration_seconds,
                    delivery_error='计算已有完成回执，文件交付尚未完成', delivery_state='pending')
        if outcome.receipt is not None:
            info['process_receipt'] = outcome.receipt
            from services.file_services.file_helper import atomic_write
            atomic_write(self.fs.run_dir(run_id) / 'done.json', lambda f: f.write(json.dumps(outcome.receipt).encode()))
        self.fs._save_manifest()

    async def execute(self, code, *, on_event=None):
        from .lifetime import RETRIED_NOTE, RETRY_FAILED_PREFIX
        async with self._lock, self.execution_scope.operation(self, self.timeout):
            if self.pending_run:
                return CellResult(ok=False, stdout='', error='上次计算的文件尚未取回，请先调用 retry_outputs，不要重算。',
                    run_id=self.pending_run, failure_kind='delivery')
            if len(code.encode('utf-8')) > self.settings.max_file_bytes:
                from .file_limits import FileLimitExceeded
                raise FileLimitExceeded('执行代码超过单文件上限；代码尚未提交')
            note = None
            result = None
            for attempt in range(2):
                if note:
                    self._lifetime_note = note
                result = await self._execute_attempt(code, on_event=on_event)
                if result.failure_kind != 'sandbox_lost' or attempt:
                    break
                try:
                    await self._rebuild()
                except Exception as exc:
                    result.error = '重建失败：' + self._safe_error(exc)
                    result.recovery_note = result.error
                    result.failure_kind = 'sandbox_lost'
                    self._lifetime_note = None
                    return result
                note = RETRIED_NOTE
            if note and result.failure_kind == 'sandbox_lost':
                result.error = RETRY_FAILED_PREFIX + (result.error or '计算沙箱仍不可用')
                result.recovery_note = result.error
            elif note or self._lifetime_note:
                result.recovery_note = result.recovery_note or self._lifetime_note or note
            self._lifetime_note = None
            return result

    async def _execute_attempt(self, code, *, on_event=None):
            run_id = self.fs.create_run()
            self.fs.save_run_input(run_id, code)
            self.fs.save_run_stdout(run_id, "")
            self.fs.save_run_stderr(run_id, "")
            info = self.fs._manifest["runs"][run_id]
            process_job = getattr(self.session, 'execution_mode', None) == 'process'
            info.update(status="starting", provider=self.settings.provider, runtime_image=self.settings.image,runtime_owner=self.owner,
                        execution_mode='process' if process_job else 'legacy')
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
            submitted = False
            phase = 'snapshot_inputs'
            def stage(name):
                nonlocal phase
                import time
                phase=name
                info.update(phase=name,phase_started_at=time.time())
                self.fs._save_manifest()
            await event("starting")
            try:
                from .artifacts import output_hashes
                from services.file_services.workspace_lock import edit_lock
                with edit_lock(self.workspace_dir):
                    info['local_output_baseline'] = output_hashes(self.fs, max_file_bytes=self.settings.max_file_bytes)
                    self._freeze_inputs(run_id)
                stage('start_runtime')
                await asyncio.wait_for(self._start(), self.settings.startup_timeout)
                # Persist identity before any fallible preparation/transfer.
                info.update(sandbox_id=self.session.id,context_id=self.session.context_id,
                    cache_key=self.session.cache_key,runtime_epoch=(self.runtime.handle or {}).get('epoch'),
                    submission_state='not_submitted',compute_state='not_started',delivery_state='pending')
                stage('sync_inputs')
                await asyncio.wait_for(self.transfer.sync_inputs(run_id), self.settings.startup_timeout)
                stage('snapshot_remote_inputs')
                await asyncio.wait_for(self.transfer.freeze_actual_inputs(run_id), self.settings.startup_timeout)
                inventory=await self.session.inventory(kind='runtime')
                info['output_baseline']={row['path']:row['sha256'] for row in inventory}
                info['cache_key']=self.session.cache_key
                info.update(status="running", sandbox_id=self.session.id,
                            sandbox_root=getattr(self.session, 'root', None),
                            context_id=getattr(self.session, 'context_id', None),
                            pending_files=None)
                self.fs._save_manifest()
                await event("running")
                stage('execute')
                submitted=True
                info.update(submission_state='submitting',compute_state='running')
                self.fs._save_manifest()
                execution = (self.session.execute(code, run_id, output, timeout=self.timeout) if process_job
                             else self.session.execute(code, run_id, output))
                outcome = await asyncio.wait_for(execution, self.timeout)
                self._record_outcome(run_id, outcome, result)
                execution_finished = True
                # Commit the execution receipt before any fallible output preparation.
                info.update(status='success' if result.ok else 'error', error=result.error,
                            failure_kind=result.failure_kind, finalize_pending=True,
                            delivery_error='计算已结束，文件交付尚未完成')
                self.fs._save_manifest()
                stage('finalize_outputs')
                try:
                    await self._finalize(run_id)
                except Exception as exc:
                    result.delivery_error = self._safe_error(exc)
                    info['failure_stage']='finalize_outputs'
                stage('deliver_outputs')
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
                    cancelled = True
                    abnormal = submitted
                    result.error = "执行已取消"
                    result.failure_kind = 'cancelled'
                raise
            except asyncio.TimeoutError:
                abnormal = submitted and not execution_finished
                if execution_finished:
                    result.delivery_error='计算已有完成回执，文件处理超时；请恢复交付，不重算'
                    self.pending_run=run_id
                else:
                    result.timed_out = True
                    result.failure_kind = 'timeout'
                    result.error = ("代码执行超时，正在停止当前进程并补取日志和文件" if submitted else
                                    '执行前准备超时（'+phase+'）；代码尚未提交，健康内核保留，可重试准备')
            except Exception as exc:
                abnormal = submitted and not execution_finished
                # Provider failures can contain connection headers. Never echo API keys.
                from .lifetime import readable_sandbox_error, replayable_sandbox_loss
                if execution_finished:
                    result.delivery_error=self._safe_error(exc)
                    self.pending_run=run_id
                elif (not submitted or not process_job) and replayable_sandbox_loss(exc, execution_finished=False):
                    result.failure_kind = 'sandbox_lost'
                    result.error = readable_sandbox_error(exc) or self._safe_error(exc)
                else:
                    result.error = self._safe_error(exc)
                    from .file_limits import FileLimitExceeded
                    result.failure_kind = 'resource' if isinstance(exc, FileLimitExceeded) else 'transport_unknown' if submitted else 'preparation'
            finally:
                if abnormal and process_job:
                    # Never delete a healthy sandbox because a status/log read
                    # failed. Stop only the job, then retrieve its final bytes.
                    try:
                        outcome = await asyncio.wait_for(self.session.interrupt(
                            'timeout' if result.timed_out else 'cancelled'), 30)
                        if outcome is None:
                            raise RuntimeError('原任务尚无停止回执')
                        self._record_outcome(run_id, outcome, result)
                        execution_finished = True
                        result.files = await asyncio.wait_for(self._collect(run_id), 60)
                    except Exception as exc:
                        result.delivery_error = '进程停止或文件回收尚未确认，保留沙箱和原任务：' + self._safe_error(exc)
                        self.pending_run = run_id
                info.update(failure_stage=('execute' if execution_finished and result.error else
                    info.get('failure_stage') or phase) if result.error or result.delivery_error else None,
                    submission_state=('acknowledged' if execution_finished else 'unknown' if submitted else 'not_submitted'),
                    compute_state=('timed_out' if result.timed_out else 'cancelled' if cancelled else 'code_error' if result.error else 'succeeded') if execution_finished else
                                  'unknown' if submitted and process_job else 'timed_out' if submitted and result.timed_out else 'unknown' if submitted else 'not_started',
                    delivery_state='pending' if result.delivery_error else 'available' if execution_finished else 'not_started')
                if result.failure_kind == 'sandbox_lost':
                    # The server already dropped this runtime. Destroying it can stick
                    # the lease in an unconfirmed state and block the one rebuild.
                    self._started = False
                    self.transfer.seen = None
                elif abnormal and not process_job:
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
                    remaining = max(0, self.settings.max_log_bytes - size)
                    diagnostic = ('\n' + result.error).encode('utf-8')[:remaining].decode('utf-8', errors='ignore')
                    with (self.fs.run_dir(run_id) / "stderr.log").open("a", encoding="utf-8") as stream:
                        stream.write(diagnostic)
                status = "cancelled" if cancelled else "timeout" if result.timed_out else "success" if result.ok else "error"
                info.update(error=result.error, files=result.files, delivery_error=result.delivery_error,
                            failure_kind=result.failure_kind, context_restored=getattr(self.session, 'context_restored', False))
                self.fs.finish_run(run_id, status)
                if execution_finished and not result.delivery_error and status in {'success', 'error'}:
                    try:
                        from .evidence import seal_run
                        seal_run(self.fs, run_id)
                    except Exception as exc:
                        result.delivery_error = self._safe_error(exc)
                        info['delivery_error'] = result.delivery_error
                        info['delivery_state'] = 'pending'
                        self.fs._save_manifest()
                        self.pending_run = run_id
                await event("finished", status=status, error=result.error, files=result.files,
                            delivery_error=result.delivery_error)
            return result

    def _safe_error(self, error):
        text = str(error)
        text = text.replace(self.settings.api_key, '[redacted]') if self.settings.api_key else text
        return text.encode('utf-8')[:self.settings.max_log_bytes].decode('utf-8', errors='ignore')

    async def retry_outputs(self):
        from .evidence import seal_run
        async with self._lock, self.execution_scope.operation(self, self.timeout):
            if not self.pending_run:
                return None
            run_id = self.pending_run
            info = self.fs.get_run_info(run_id)
            if info.get('provider', 'opensandbox') != self.settings.provider:
                raise RuntimeError('原执行属于另一沙箱平台；请恢复原平台配置取回文件，不会跨平台重跑')
            if info.get('cache_key')!=self.session.cache_key:
                raise RuntimeError('原执行缓存不可用；请恢复原缓存配置取回文件，不会重跑计算')
            # Persistent files survive containers. In-memory figures do not.
            recovering = not self._started
            if recovering:
                await asyncio.wait_for(self._start(), self.settings.startup_timeout)
                if info.get('cache_key')!=self.session.cache_key:
                    raise RuntimeError('原执行缓存不可用；不会重跑计算')
            if info.get('execution_mode') == 'process' and not info.get('process_receipt'):
                if getattr(self.session, 'execution_mode', None) != 'process':
                    raise RuntimeError('原任务需要支持进程回执的沙箱，不能重放代码')
                # Reconstruct logs from byte zero after a backend restart. This
                # replaces the partial copy instead of appending duplicate data.
                import tempfile
                import os
                with tempfile.TemporaryDirectory(dir=self.fs.run_dir(run_id), prefix='.recover-logs-') as folder:
                    recovered_logs = Path(folder)
                    for channel in ('stdout', 'stderr'):
                        (recovered_logs / (channel + '.log')).touch()
                    size = 0
                    async def output(channel, text):
                        nonlocal size
                        raw = text.encode('utf-8')
                        size += len(raw)
                        if size > self.settings.max_log_bytes:
                            raise ValueError('恢复日志超过限额，原本地日志保留')
                        with (recovered_logs / (channel + '.log')).open('ab') as stream:
                            stream.write(raw)
                    outcome = await asyncio.wait_for(self.session.recover(run_id, output), self.timeout)
                    for channel in ('stdout', 'stderr'):
                        os.replace(recovered_logs / (channel + '.log'), self.fs.run_dir(run_id) / (channel + '.log'))
                result = CellResult(ok=False, stdout='', error=None, run_id=run_id)
                self._record_outcome(run_id, outcome, result)
                info.update(error=result.error, failure_kind=result.failure_kind, finalize_pending=False)
                self.fs.finish_run(run_id, 'timeout' if result.timed_out else
                                   'cancelled' if outcome.stop_reason == 'cancelled' else 'success' if result.ok else 'error')
            if info.get('finalize_pending'):
                if info.get('execution_mode') != 'process' and (not self._started or info.get('context_id') != getattr(self.session, 'context_id', None)):
                    raise RuntimeError('原 Python 内存已失效，无法确认尚未保存的图；保留真实未完成状态，不重跑')
                await self._finalize(run_id)
            # A disk-backed completion may be frozen for the first time after
            # reconnecting. Legacy kernels still require the original manifest.
            info['files'] = await self._collect(run_id, recover=recovering and info.get('execution_mode') != 'process')
            if info.get('status') in {'success', 'error'}:
                seal_run(self.fs, run_id)
            info['delivery_error'] = None
            info['delivery_state'] = 'available'
            if not info.get('error'):info['failure_stage']=None
            self.fs._save_manifest()
            self.pending_run = None
            return run_id

    async def close(self):
        async with self._lock, self.execution_scope.operation(self,self.timeout):
            try:
                await self._stop()
            finally:
                await self.execution_scope.release(self)

    async def disconnect(self):
        """Release client resources without discarding the remote interpreter."""
        async with self._lock:
            try:
                await self.runtime.disconnect()
            finally:
                self._started=False
                self.transfer.seen=None
                await self.execution_scope.release(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
