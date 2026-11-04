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


def remote_job_running(handle, run_id, row):
    """Whether a job left by another backend process is still executing.

    False: it never started, or the remote process is gone. None: this process
    cannot see the remote process. A live job inside this process is tracked
    separately and does not use this probe.
    """
    if not _job_submitted(row):
        return False
    return None


def _record_idle(row, handle, now, ttl):
    for source in (row, handle or {}):
        for key in ('phase_started_at', 'updated_at', 'started_at', 'last_job_at'):
            stamp = source.get(key)
            if stamp is None:
                continue
            try:
                return now - float(stamp) >= float(ttl)
            except (TypeError, ValueError):
                continue
    return True


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


def _manifest_busy(handle, *, now, ttl):
    """True when a job this process does not own must keep the sandbox.

    A previous process's ``starting`` / ``not_submitted`` row is not a running
    job. A submitted job protects the sandbox only while its remote process is
    still running, or until the idle limit when that cannot be seen. Stale rows
    are marked interrupted so later sweeps do not treat them as live.
    """
    manifest_path = _manifest_path(handle)
    if manifest_path is None or not manifest_path.is_file():
        return False
    try:
        data = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        return True
    runs = data.get('runs') or {}
    busy = False
    changed = False
    stale_ids = []
    for run_id, row in runs.items():
        if not isinstance(row, dict):
            continue
        if row.get('process_receipt') or row.get('submission_state') == 'acknowledged':
            continue
        if row.get('status') in {'interrupted', 'error', 'success', 'cancelled', 'timeout'}:
            continue
        if run_id in _ACTIVE_RUNS:
            busy = True
            continue
        submitted = _job_submitted(row)
        alive = remote_job_running(handle, run_id, row)
        if alive is True:
            busy = True
            continue
        if submitted and alive is None and not _record_idle(row, handle, now, ttl):
            busy = True
            continue
        _mark_stale(row, submitted=submitted)
        stale_ids.append(run_id)
        changed = True
    if changed:
        _write_json(manifest_path, data)
    if stale_ids and handle.get('_path'):
        ids = [run_id for run_id in (handle.get('active_run_ids') or []) if run_id not in stale_ids]
        if ids != list(handle.get('active_run_ids') or []):
            handle['active_run_ids'] = ids
            _write_json(handle['_path'], handle)
    return busy


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
    if _manifest_busy(handle, now=now, ttl=ttl):
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


async def _release(view, reason, *, activity, directory, settings, immediate, now, ttl):
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
    activity = load_activity()
    remote = await directory.list_sandboxes() if directory is not None else []
    released = []
    immediate = force_reason in IMMEDIATE_REASONS
    for view in _candidates(remote, activity, work_id=work_id):
        handle = activity.get(view.id) or {}
        reason = force_reason if force_reason in {'paused', 'completed'} and (handle.get('role') or (view.metadata or {}).get('role') or 'research') != 'run' else None
        if reason is None:
            reason = decide(view, projects=projects, activity=handle, now=now, ttl=ttl)
        if reason is None:
            continue
        if await _release(view, reason, activity=activity, directory=directory, settings=settings,
                          immediate=immediate or reason in IMMEDIATE_REASONS, now=now, ttl=ttl):
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
