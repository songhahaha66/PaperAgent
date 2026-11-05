"""Reclaim idle project research sandboxes and their egress companions.

OpenSandbox keeps a long-lived research sandbox per project. Disconnecting the
client leaves that sandbox and its ``sandbox-egress-*`` companion running.
Per-run sandboxes are already destroyed by their callers; this module deletes
the project sandbox when it is idle, when the run is paused or completed, or
when the project is deleted. The next execution creates a new one.

Killing the sandbox through the existing runtime lifecycle (or the OpenSandbox
admin API) removes the egress companion. Sandboxes are selected by metadata,
not by container name. A sandbox with a running job is left alone.
"""
import asyncio
import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

APPLICATION = 'paperagent'
ACTIVE_PROJECT_STATUSES = frozenset({'in_progress', 'running', 'processing', 'waiting'})
IMMEDIATE_REASONS = frozenset({'paused', 'completed', 'deleted'})
DEFAULT_IDLE_SECONDS = 900
DEFAULT_SWEEP_SECONDS = 180
_TERMINAL_STATES = frozenset({'terminated'})

_guard = threading.Lock()
_BUSY_KEYS = set()
_BUSY_COUNTS = {}
_RECLAIMING = set()
_RECLAIMING_KEYS = set()
_LIVE = {}
_ACTIVE_RUNS = set()
_LOOP = None
_LOOP_THREAD = None
# Kubernetes label values: <=63, alphanumeric ends, and only [-A-Za-z0-9_.].
_LABEL_VALUE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?$')


@dataclass(frozen=True)
class SandboxView:
    id: str
    metadata: dict = field(default_factory=dict)
    created_at: float | None = None
    state: str = ''


def idle_ttl_seconds(environ=None) -> int:
    """Idle time with no new job before a research sandbox is deleted."""
    raw = (environ or os.environ).get('PAPERAGENT_SANDBOX_IDLE_TTL_SECONDS', str(DEFAULT_IDLE_SECONDS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_IDLE_SECONDS
    return value if value > 0 else DEFAULT_IDLE_SECONDS


def sweep_interval_seconds(environ=None) -> int:
    raw = (environ or os.environ).get('PAPERAGENT_SANDBOX_SWEEP_SECONDS', str(DEFAULT_SWEEP_SECONDS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SWEEP_SECONDS
    return value if value > 0 else DEFAULT_SWEEP_SECONDS


def label_value(value) -> str:
    """A metadata string the OpenSandbox server accepts as a label."""
    text = '' if value is None else str(value)
    if re.fullmatch(r'[a-f0-9]{64}', text):
        text = text[:32]
    text = re.sub(r'[^A-Za-z0-9_.-]', '-', text)
    text = re.sub(r'[-_.]{2,}', '-', text).strip('-_.')
    if len(text) > 63:
        text = text[:63].strip('-_.')
    if not text or not text[0].isalnum():
        text = ('x' + text)[:63].strip('-_.')
    if text and not text[-1].isalnum():
        text = (text[:-1] + 'x')[:63]
    if not text or not _LABEL_VALUE.fullmatch(text):
        return 'x'
    return text


def cache_labels(cache_key):
    """Full cache identity plus the 32-char label form used in metadata."""
    if not cache_key:
        return []
    text = str(cache_key)
    labels = [text]
    short = text[:32] if re.fullmatch(r'[a-f0-9]{64}', text) else None
    if short and short not in labels:
        labels.append(short)
    return labels


def sandbox_metadata(*, work_id, experiment_id, cache_key) -> dict:
    """Labels stored on the OpenSandbox record. Listing filters on these.

    Values are Kubernetes-style labels. A 64-char cache digest does not fit,
    so the server record keeps only its first 32 hex characters. Runtime files
    and volume paths still use the full digest.
    """
    role = 'research' if experiment_id in {None, 'default', 'research'} else 'run'
    meta = {
        'application': label_value(APPLICATION),
        'role': label_value(role),
        'cache_key': label_value(cache_key),
    }
    if work_id:
        meta['work_id'] = label_value(work_id)
    return meta


def bind_loop(loop):
    global _LOOP, _LOOP_THREAD
    _LOOP = loop
    _LOOP_THREAD = threading.get_ident()


def register_kernel(kernel):
    _LIVE[id(kernel)] = kernel


def unregister_kernel(kernel):
    _LIVE.pop(id(kernel), None)


def hold_key(cache_key):
    """Keep a sandbox that is being created or is inside a compile."""
    if not cache_key:
        return
    with _guard:
        for alias in cache_labels(cache_key):
            _BUSY_KEYS.add(alias)


def release_key(cache_key):
    if not cache_key:
        return
    with _guard:
        for alias in cache_labels(cache_key):
            _BUSY_KEYS.discard(alias)


def hold_sandbox(sandbox_id):
    if not sandbox_id:
        return
    with _guard:
        _BUSY_COUNTS[sandbox_id] = _BUSY_COUNTS.get(sandbox_id, 0) + 1


def release_sandbox(sandbox_id):
    if not sandbox_id:
        return
    with _guard:
        count = _BUSY_COUNTS.get(sandbox_id, 0) - 1
        if count <= 0:
            _BUSY_COUNTS.pop(sandbox_id, None)
        else:
            _BUSY_COUNTS[sandbox_id] = count


def hold_runtime(kernel) -> bool:
    """Mark this kernel busy. False when a reclaim of its sandbox is in progress."""
    key = getattr(getattr(kernel, 'session', None), 'cache_key', None)
    if not key:
        return True
    aliases = cache_labels(key)
    with _guard:
        if any(alias in _RECLAIMING_KEYS for alias in aliases):
            return False
        for alias in aliases:
            _BUSY_KEYS.add(alias)
        return True


def release_runtime(kernel):
    release_key(getattr(getattr(kernel, 'session', None), 'cache_key', None))


@asynccontextmanager
async def runtime_hold(kernel):
    """Hold the project sandbox for one call so an idle sweep cannot delete it."""
    if not await acquire_runtime(kernel):
        raise RuntimeError('研究沙箱正在回收，请稍后重试')
    try:
        yield
    finally:
        release_runtime(kernel)


async def acquire_runtime(kernel):
    """Wait out an in-flight reclaim, then hold the runtime for this call."""
    waited = False
    for _ in range(100):
        if hold_runtime(kernel):
            if waited:
                kernel._mark_reclaimed()
            return True
        waited = True
        await asyncio.sleep(0.05)
    return False


def remember_submission(kernel, run_id):
    if run_id:
        with _guard:
            _ACTIVE_RUNS.add(run_id)
    _remember(kernel, run_id, active=True)


def remember_finished(kernel, run_id):
    if run_id:
        with _guard:
            _ACTIVE_RUNS.discard(run_id)
    _remember(kernel, run_id, active=False)


def _remember(kernel, run_id, *, active):
    runtime = getattr(kernel, 'runtime', None)
    session = getattr(kernel, 'session', None)
    if runtime is None or not run_id:
        return
    handle = runtime.handle or {}
    ids = [item for item in (handle.get('active_run_ids') or []) if item != run_id]
    if active:
        ids.append(run_id)
    experiment = getattr(session, 'experiment_id', None)
    values = {
        'last_job_at': time.time(),
        'active_run_ids': ids,
        'role': 'research' if experiment in {None, 'default', 'research'} else 'run',
        'cache_key': getattr(session, 'cache_key', None),
    }
    work_id = getattr(session, 'work_id', None)
    if work_id:
        values['work_id'] = str(work_id)
    workspace = getattr(session, 'workspace_dir', None) or getattr(runtime, 'workspace', None)
    if workspace:
        values['workspace'] = str(Path(workspace).absolute())
    sandbox_id = getattr(session, 'id', None)
    if sandbox_id:
        values['sandbox_id'] = sandbox_id
    try:
        runtime.save(**{key: value for key, value in values.items() if value is not None})
    except Exception:
        logger.warning('记录沙箱活动失败 project_id=%s', work_id or '-', exc_info=True)


def decide(view: SandboxView, *, projects, activity, now, ttl) -> str | None:
    """Why this sandbox should be deleted, or None to keep it.

    ``projects`` maps work id to status. None means the database could not be
    read, so only the idle clock applies. A per-run sandbox of a paused project
    stays until it is idle; the long-lived research sandbox does not.
    """
    meta = view.metadata or {}
    if meta.get('application') not in {None, APPLICATION}:
        return None
    info = activity or {}
    work_id = meta.get('work_id') or info.get('work_id') or None
    role = meta.get('role') or info.get('role') or 'research'
    last = info.get('last_job_at')
    if last is None:
        last = view.created_at
    idle = last is not None and now - float(last) >= float(ttl)
    if (view.state or '').lower() in _TERMINAL_STATES:
        return 'gone'
    if projects is None:
        return 'idle' if idle else None
    if not work_id:
        return 'orphan' if idle else None
    if work_id not in projects:
        return 'deleted'
    status = projects[work_id]
    if role != 'run' and status in {'paused', 'completed'}:
        return status
    if role != 'run' and status not in ACTIVE_PROJECT_STATUSES:
        return 'inactive'
    if idle:
        return 'idle'
    return None


def _epoch(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if hasattr(value, 'timestamp'):
        return float(value.timestamp())
    return None


def _state_root():
    from config.paths import get_pa_data_base
    return get_pa_data_base() / 'project-state'


def load_activity():
    """Runtime handles keyed by the sandbox id they still point at."""
    found = {}
    root = _state_root()
    if not root.is_dir():
        return found
    for path in root.glob('*/.system/runtimes/*.json'):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        data = dict(data)
        data['_path'] = str(path)
        sandbox_id = data.get('sandbox_id')
        if sandbox_id:
            found[sandbox_id] = data
    return found


def _job_submitted(row):
    """A process job that was actually handed to the sandbox."""
    state = row.get('submission_state')
    if state in {'submitting', 'unknown', 'submitted'}:
        return True
    if row.get('status') == 'running' and state not in {None, 'not_submitted'}:
        return True
    if row.get('compute_state') in {'running', 'unknown'} and state not in {None, 'not_submitted'}:
        return True
    return False


async def remote_job_running(handle, run_id, row, session):
    """Unknown is protective; elapsed time is never evidence of process exit."""
    if not _job_submitted(row):
        return False
    if row.get('execution_mode') != 'process':
        return None
    try:
        return await asyncio.wait_for(session.job_status(run_id), 15)
    except Exception:
        return None


def recovery_retention_seconds():
    try:
        return max(900, int(os.environ.get('PAPERAGENT_SANDBOX_RECOVERY_TTL_SECONDS', '86400')))
    except ValueError:
        return 86400


async def _recover_delivery(handle, run_id, session, settings):
    from .remote_kernel import RemoteKernel
    kernel = RemoteKernel(handle['workspace'], session, settings)
    kernel.runtime.handle = {k: v for k, v in handle.items() if k != '_path'}
    kernel._started = True  # Attach to this identity; never create or replay a job.
    kernel.pending_run = run_id
    await kernel._retry_outputs_locked(renew=False)


async def _manifest_busy(handle, *, now, ttl, settings):
    """Called with the cross-process execution lock held, including recovery."""
    inspected_at = time.monotonic()
    initial_now = now
    path = _manifest_path(handle)
    if path is None or not path.is_file():
        return bool(handle.get('active_run_ids'))
    try:
        data = json.loads(path.read_text())
        runs = data['runs']
        if not isinstance(runs, dict):
            return True
    except (OSError, ValueError, KeyError):
        return True
    session = None
    try:
        for run_id, row in runs.items():
            now = initial_now + (time.monotonic() - inspected_at)
            if not isinstance(row, dict):
                return True
            if run_id in data.get('inherited_runs', []):
                continue
            if row.get('sandbox_id') and row['sandbox_id'] != handle.get('sandbox_id'):
                continue
            if row.get('cache_key') and row['cache_key'] != handle.get('cache_key'):
                continue
            submitted = _job_submitted(row)
            if row.get('submission_state') == 'not_submitted' and not row.get('process_receipt'):
                _mark_stale(row, submitted=False)
                row.update(delivery_state='not_started', delivery_error=None)
                _write_json(path, data)
                continue
            pending = (row.get('delivery_state') == 'pending' or row.get('delivery_error') or
                       row.get('finalize_pending'))
            if row.get('delivery_state') == 'available' and not pending:
                continue
            if not submitted and not pending:
                if row.get('status') in {'starting', 'running', 'created'}:
                    _mark_stale(row, submitted=False)
                    _write_json(path, data)
                continue
            # A receipt is not a delivery acknowledgement. Legacy completed rows
            # without pending markers need no recovery.
            if row.get('submission_state') == 'acknowledged' and not pending:
                continue
            deadline = row.get('recovery_deadline')
            if deadline is not None and now >= deadline:
                return True
            attempt_started = time.monotonic()
            try:
                if session is None:
                    from .factory import create_session
                    session = create_session(handle['workspace'], settings=settings,
                        work_id=handle.get('work_id'), experiment_id='research', cache_key=handle['cache_key'])
                    # Status/transfer RPCs also run commands with sustain loops.
                    # Disable implicit renewal for the entire sweeper session.
                    session.automatic_renewal = False
                    await asyncio.wait_for(session.attach_for_destroy(handle), 15)
                alive = await remote_job_running(handle, run_id, row, session)
                if alive is not False:
                    return True
                remaining = 90 if deadline is None else deadline - now - (time.monotonic() - attempt_started)
                if remaining <= 0:
                    return True
                await asyncio.wait_for(_recover_delivery(handle, run_id, session, settings), min(90, remaining))
                data = json.loads(path.read_text())
            except Exception as exc:
                logger.warning('沙箱结果恢复未完成 run_id=%s error=%s', run_id, type(exc).__name__)
                # Recovery may have committed a receipt before failing delivery.
                # Reload so this error cannot overwrite that progress.
                data = json.loads(path.read_text())
                current = data['runs'][run_id]
                attempted_at = now + (time.monotonic() - attempt_started)
                current.setdefault('recovery_started_at', attempted_at)
                current.setdefault('recovery_deadline', attempted_at + recovery_retention_seconds())
                current.update(delivery_state='pending', recovery_attempted_at=attempted_at,
                               delivery_error='结果恢复未完成：' + type(exc).__name__)
                _write_json(path, data)
                # Bound extra retention without declaring failed delivery complete.
                # After the deadline, leave evidence intact and allow server TTL
                # expiry; never forcibly kill a job whose status is uncertain.
                if session is not None and attempted_at < current['recovery_deadline']:
                    try:
                        remaining = current['recovery_deadline'] - attempted_at
                        if remaining >= 1:
                            await asyncio.wait_for(session.renew(min(settings.max_timeout, int(remaining))), min(15, remaining))
                    except Exception:
                        pass
                return True
        return False
    finally:
        if session is not None:
            try:
                await asyncio.wait_for(session.disconnect(), 5)
            except Exception:
                pass


def _mark_stale(row, *, submitted):
    if row.get('status') in {'interrupted', 'error', 'success', 'cancelled', 'timeout'}:
        return
    row['status'] = 'interrupted'
    row['failure_kind'] = 'transport_unknown' if submitted else 'preparation'
    row['error'] = ('后端退出后远端任务已不在运行，完成情况未确认'
                    if submitted else '后端在提交任务前退出，代码未执行')
    row.setdefault('finished_at', time.time())


def _manifest_path(handle):
    path = handle.get('_path') if handle else None
    if not path:
        return None
    try:
        return Path(path).parents[2] / 'manifest.json'
    except IndexError:
        return None


def _write_json(path, data):
    from services.file_services.file_helper import atomic_write
    payload = {key: value for key, value in data.items() if key != '_path'}
    atomic_write(Path(path), lambda stream: stream.write(json.dumps(payload).encode()))



def _live_locked(sandbox_id, cache_key):
    for kernel in _LIVE.values():
        session = getattr(kernel, 'session', None)
        same = getattr(session, 'id', None) == sandbox_id or (cache_key and getattr(session, 'cache_key', None) == cache_key)
        if not same:
            continue
        lock = getattr(kernel, '_lock', None)
        if lock is not None and lock.locked():
            return True
    return False


def _cache_aliases(view, handle):
    aliases = []
    for key in (handle.get('cache_key'), (view.metadata or {}).get('cache_key')):
        aliases.extend(cache_labels(key))
    return aliases


def _protected(view, handle, *, now, ttl):
    """Caller holds ``_guard`` for the in-process sets. Manifest reads are local."""
    if view.id and _BUSY_COUNTS.get(view.id, 0) > 0:
        return True
    if any(key and key in _BUSY_KEYS for key in _cache_aliases(view, handle)):
        return True
    if any(run_id in _ACTIVE_RUNS for run_id in (handle.get('active_run_ids') or [])):
        return True
    if _live_locked(view.id, handle.get('cache_key') or (view.metadata or {}).get('cache_key')):
        return True
    return False


def _begin(view, handle, *, now, ttl) -> bool:
    with _guard:
        if view.id in _RECLAIMING or _protected(view, handle, now=now, ttl=ttl):
            return False
        _RECLAIMING.add(view.id)
        for key in _cache_aliases(view, handle):
            _RECLAIMING_KEYS.add(key)
        return True


def _end(view, handle):
    with _guard:
        _RECLAIMING.discard(view.id)
        for key in _cache_aliases(view, handle):
            _RECLAIMING_KEYS.discard(key)


def _project_id(view, handle):
    return (handle or {}).get('work_id') or (view.metadata or {}).get('work_id') or '-'


def _skip(view, handle, reason, *, immediate):
    message = '跳过回收研究沙箱 project_id=%s sandbox_id=%s reason=%s：任务仍在运行'
    project_id = _project_id(view, handle)
    if immediate:
        logger.info(message, project_id, view.id, reason)
    else:
        logger.debug(message, project_id, view.id, reason)


def _gone(exc) -> bool:
    status = getattr(exc, 'status_code', None)
    text = str(exc).lower()
    return status == 404 or 'not found' in text or '不存在' in text


def _clear_file(path, reason):
    from services.file_services.file_helper import atomic_write
    file = Path(path)
    if not file.is_file():
        return
    try:
        data = json.loads(file.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    data.update(state='destroyed', sandbox_id=None, context_id=None, active_run_ids=[],
                expires_at=time.time(), reclaimed_reason=reason)
    atomic_write(file, lambda stream: stream.write(json.dumps(data).encode()))


def _drop_leases(sandbox_id):
    if not sandbox_id:
        return
    root = _state_root()
    if not root.is_dir():
        return
    from services.file_services.file_helper import atomic_write
    from .runtime_manager import LeaseLedger
    for path in root.glob('*/sandbox-lease.json'):
        try:
            with LeaseLedger.locked(path):
                if not path.is_file():
                    continue
                data = json.loads(path.read_text())
                leases = dict(data.get('leases') or {})
                if data and 'leases' not in data and data.get('sandbox_id'):
                    leases = {'previous': data}
                removed = [key for key, row in leases.items()
                           if isinstance(row, dict) and row.get('sandbox_id') == sandbox_id]
                if not removed:
                    continue
                for key in removed:
                    leases.pop(key, None)
                if not leases:
                    path.unlink(missing_ok=True)
                    continue
                atomic_write(path, lambda stream: stream.write(json.dumps({
                    'leases': leases,
                    'expires_at': max(row.get('expires_at', 0) for row in leases.values()),
                }).encode()))
        except Exception:
            logger.warning('清理沙箱租约失败 sandbox_id=%s', sandbox_id, exc_info=True)


def _notify(sandbox_id, cache_key):
    for kernel in list(_LIVE.values()):
        session = getattr(kernel, 'session', None)
        same = getattr(session, 'id', None) == sandbox_id or (cache_key and getattr(session, 'cache_key', None) == cache_key)
        if not same:
            continue
        mark = getattr(kernel, '_mark_reclaimed', None)
        if mark:
            mark()
        runtime = getattr(kernel, 'runtime', None)
        path = getattr(runtime, 'path', None)
        if runtime is not None and path is not None and Path(path).is_file():
            try:
                runtime.handle = json.loads(Path(path).read_text())
            except (OSError, ValueError):
                runtime.handle = None
        if getattr(session, 'id', None) == sandbox_id:
            session.id = None


async def _destroy_local(handle, settings):
    workspace = handle.get('workspace')
    cache_key = handle.get('cache_key')
    if not workspace or not cache_key or settings is None:
        return False
    from .factory import create_session
    from .runtime_manager import RuntimeManager
    experiment = 'research' if handle.get('role') != 'run' else 'reclaim-run'
    session = create_session(workspace, settings=settings, work_id=handle.get('work_id'),
                             experiment_id=experiment, cache_key=cache_key)
    runtime = RuntimeManager(workspace, session, settings)
    await runtime.destroy()
    return True


async def _destroy(view, handle, directory, settings):
    live = [kernel for kernel in list(_LIVE.values())
            if getattr(getattr(kernel, 'session', None), 'id', None) == view.id]
    try:
        if live:
            live[0].runtime.handle = json.loads(Path(handle['_path']).read_text()) if handle.get('_path') else handle
            await live[0].runtime.destroy()
            for kernel in live:
                mark = getattr(kernel, '_mark_reclaimed', None)
                if mark:
                    mark()
            return True
        if handle.get('workspace') and handle.get('cache_key'):
            if await _destroy_local(handle, settings):
                return True
    except Exception:
        logger.warning('按生命周期销毁沙箱失败 project_id=%s sandbox_id=%s',
                       _project_id(view, handle), view.id, exc_info=True)
    if directory is None:
        return False
    try:
        await directory.kill(view.id)
        return True
    except Exception as exc:
        if _gone(exc):
            return True
        logger.warning('删除沙箱失败 project_id=%s sandbox_id=%s', _project_id(view, handle), view.id, exc_info=True)
        return False


def _view_from_handle(handle) -> SandboxView:
    return SandboxView(
        id=handle['sandbox_id'],
        metadata={'application': APPLICATION, 'role': handle.get('role') or 'research',
                  'work_id': handle.get('work_id'), 'cache_key': handle.get('cache_key')},
        created_at=_epoch(handle.get('last_job_at')),
        state=handle.get('state') or '',
    )


def _candidates(remote, activity, *, work_id=None):
    chosen = {}
    for view in remote:
        owner = (view.metadata or {}).get('work_id') or activity.get(view.id, {}).get('work_id')
        if work_id and owner != work_id:
            continue
        chosen[view.id] = view
    for sandbox_id, handle in activity.items():
        if work_id and handle.get('work_id') != work_id:
            continue
        chosen.setdefault(sandbox_id, _view_from_handle(handle))
    return list(chosen.values())


async def _release(view, reason, *, activity, directory, settings, immediate, now, ttl, projects):
    from services.file_services.workspace_lock import try_execution_lock
    handle = activity.get(view.id) or {}
    workspace = handle.get('workspace')
    if not workspace:
        work_id = (view.metadata or {}).get('work_id')
        if work_id:
            from config.paths import get_workspace_path
            workspace = get_workspace_path(work_id)
    if not workspace:
        # Unowned remote orphans have no local execution identity to lock.
        return await _release_locked(view, reason, activity=activity, directory=directory,
            settings=settings, immediate=immediate, now=now, ttl=ttl)
    with try_execution_lock(workspace) as acquired:
        if not acquired:
            return False
        fresh = load_activity()
        if handle and view.id not in fresh:
            return False
        handle = fresh.get(view.id) or {}
        # A run may have completed since the listing; use its updated idle clock.
        reason = decide(view, projects=projects, activity=handle, now=now, ttl=ttl)
        if reason is None:
            return False
        return await _release_locked(view, reason, activity=fresh, directory=directory,
            settings=settings, immediate=immediate, now=now, ttl=ttl)


async def _release_locked(view, reason, *, activity, directory, settings, immediate, now, ttl):
    handle = activity.get(view.id) or {}
    if not _begin(view, handle, now=now, ttl=ttl):
        _skip(view, handle, reason, immediate=immediate)
        return False
    try:
        with _guard:
            protected = _protected(view, handle, now=now, ttl=ttl)
        if protected:
            _skip(view, handle, reason, immediate=immediate)
            return False
        if await _manifest_busy(handle, now=now, ttl=ttl, settings=settings):
            return False
        if not await _destroy(view, handle, directory, settings):
            return False
        for row in activity.values():
            if row.get('sandbox_id') == view.id and row.get('_path'):
                _clear_file(row['_path'], reason)
        _drop_leases(view.id)
        _notify(view.id, handle.get('cache_key') or (view.metadata or {}).get('cache_key'))
        logger.info('已回收研究沙箱及其出口容器 project_id=%s sandbox_id=%s reason=%s',
                    _project_id(view, handle), view.id, reason)
        return True
    finally:
        _end(view, handle)


async def reclaim_recorded(*, directory, projects, now=None, ttl=None, settings=None, work_id=None, force_reason=None):
    """Delete every selected sandbox. Returns ``(sandbox_id, reason)`` pairs."""
    now = time.time() if now is None else now
    ttl = idle_ttl_seconds() if ttl is None else ttl
    sweep_started = time.monotonic()
    activity = load_activity()
    remote = await directory.list_sandboxes() if directory is not None else []
    released = []
    immediate = force_reason in IMMEDIATE_REASONS
    for view in _candidates(remote, activity, work_id=work_id):
        candidate_now = now + (time.monotonic() - sweep_started)
        handle = activity.get(view.id) or {}
        reason = force_reason if force_reason in {'paused', 'completed'} and (handle.get('role') or (view.metadata or {}).get('role') or 'research') != 'run' else None
        if reason is None:
            reason = decide(view, projects=projects, activity=handle, now=candidate_now, ttl=ttl)
        if reason is None:
            continue
        if await _release(view, reason, activity=activity, directory=directory, settings=settings,
                          immediate=immediate or reason in IMMEDIATE_REASONS, now=candidate_now, ttl=ttl, projects=projects):
            released.append((view.id, reason))
    return released


def load_settings():
    try:
        from .settings import SandboxSettings
        return SandboxSettings.from_env()
    except Exception:
        logger.warning('读取沙箱配置失败，跳过回收', exc_info=True)
        return None


def reclaim_enabled(settings) -> bool:
    return bool(settings and settings.provider == 'opensandbox' and settings.api_key and settings.endpoint)


def project_statuses():
    try:
        from database.database import SessionLocal
        from models.models import Project
        with SessionLocal() as db:
            return {row.work_id: row.status for row in db.query(Project.work_id, Project.status)}
    except Exception:
        logger.warning('读取项目状态失败，本轮只按空闲时间回收', exc_info=True)
        return None


def connection_config(settings):
    from datetime import timedelta
    from urllib.parse import urlsplit
    from opensandbox.config import ConnectionConfig
    from opensandbox.transport import RetryPolicy
    url = urlsplit(settings.endpoint)
    return ConnectionConfig(domain=url.netloc, protocol=url.scheme, api_key=settings.api_key,
                            use_server_proxy=True, retry_policy=RetryPolicy.disabled(),
                            request_timeout=timedelta(seconds=30))


class OpenSandboxDirectory:
    """List and kill by metadata. ``kill`` also removes the egress companion."""

    def __init__(self, settings):
        self.settings = settings
        self._cm = None
        self.manager = None

    async def __aenter__(self):
        from opensandbox.manager import SandboxManager
        self._cm = await SandboxManager.create(connection_config=connection_config(self.settings))
        self.manager = await self._cm.__aenter__()
        return self

    async def __aexit__(self, *exc):
        if self._cm is not None:
            return await self._cm.__aexit__(*exc)

    async def list_sandboxes(self):
        from opensandbox.models.sandboxes import SandboxFilter
        views = []
        page = None
        for _ in range(100):
            result = await self.manager.list_sandbox_infos(SandboxFilter(
                metadata={'application': APPLICATION}, page=page, page_size=50))
            for info in getattr(result, 'sandbox_infos', None) or []:
                status = getattr(info, 'status', None)
                views.append(SandboxView(
                    id=info.id,
                    metadata=dict(getattr(info, 'metadata', None) or {}),
                    created_at=_epoch(getattr(info, 'created_at', None)),
                    state=str(getattr(status, 'state', '') or ''),
                ))
            pagination = getattr(result, 'pagination', None)
            if not pagination or not getattr(pagination, 'has_next_page', False):
                break
            page = int(getattr(pagination, 'page', 0)) + 1
        return views

    async def kill(self, sandbox_id):
        await self.manager.kill_sandbox(sandbox_id)


async def reclaim_project(work_id, reason, *, directory=None, settings=None, now=None, ttl=None):
    """Delete this project's research sandbox now. A running job is kept."""
    if reason not in IMMEDIATE_REASONS or not work_id:
        return []
    settings = load_settings() if settings is None else settings
    projects = {} if reason == 'deleted' else {work_id: reason}
    own = False
    if directory is None:
        if not reclaim_enabled(settings):
            directory = None
        else:
            directory = OpenSandboxDirectory(settings)
            await directory.__aenter__()
            own = True
    try:
        return await reclaim_recorded(directory=directory, projects=projects, now=now, ttl=ttl,
                                      settings=settings, work_id=work_id, force_reason=reason)
    finally:
        if own:
            await directory.__aexit__(None, None, None)


async def sweep_once(*, directory=None, projects=None, settings=None, now=None, ttl=None):
    """Delete orphaned sandboxes and those past the idle limit."""
    settings = load_settings() if settings is None else settings
    own = False
    if directory is None:
        if not reclaim_enabled(settings):
            return []
        directory = OpenSandboxDirectory(settings)
        await directory.__aenter__()
        own = True
    if projects is None:
        projects = project_statuses()
    try:
        return await reclaim_recorded(directory=directory, projects=projects, now=now, ttl=ttl, settings=settings)
    finally:
        if own:
            await directory.__aexit__(None, None, None)


def request_reclaim(work_id, reason):
    """Schedule an immediate reclaim. Off-loop callers wait until it finishes."""
    if reason not in IMMEDIATE_REASONS or not work_id:
        return None

    async def runner():
        try:
            await reclaim_project(work_id, reason)
        except Exception:
            logger.warning('回收研究沙箱失败 project_id=%s reason=%s', work_id, reason, exc_info=True)

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    target = _LOOP if _LOOP is not None and _LOOP.is_running() else running
    if target is not None and target.is_running():
        if running is target:
            return target.create_task(runner())
        future = asyncio.run_coroutine_threadsafe(runner(), target)
        try:
            future.result(timeout=30)
        except Exception:
            logger.warning('回收研究沙箱失败 project_id=%s reason=%s', work_id, reason, exc_info=True)
        return None
    try:
        asyncio.run(runner())
    except Exception:
        logger.warning('回收研究沙箱失败 project_id=%s reason=%s', work_id, reason, exc_info=True)
    return None


async def sweep_forever():
    interval = sweep_interval_seconds()
    while True:
        await asyncio.sleep(interval)
        try:
            await sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('沙箱清扫失败')
