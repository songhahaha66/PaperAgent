"""Disk-backed process jobs, copied to the sandbox without backend credentials.

Only submit/status/stop are RPCs. The detached supervisor owns the child process,
drains its pipes to disk, enforces its deadline, and atomically commits done.json.
An ambiguous submit can be repeated with the same ID without running code twice.
"""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
import traceback


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def directory(root, job_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', job_id):
        raise ValueError('Invalid job ID')
    root = Path(root).resolve()
    target = root / '.pa-jobs' / job_id
    if (root / '.pa-jobs').is_symlink() or target.is_symlink():
        raise ValueError('Job directory must not be a symbolic link')
    target.mkdir(parents=True, exist_ok=True)
    for name in ('request.json', 'spec.json', 'submitted.json', 'process.json', 'done.json', 'stop.json',
                 'stdout.log', 'stderr.log', 'input.py', 'run.py', 'lock'):
        if (target / name).is_symlink():
            raise ValueError('Job files must not be symbolic links')
    return target


def submit(root, job_id):
    target = directory(root, job_id)
    # The supervisor inherits this flock. Closing the submitter's fd must not
    # unlock it; a retry observes the original durable submission instead.
    with (target / 'lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'job_id': job_id, 'submitted': True}
        raw = (target / 'request.json').read_bytes()
        if (target / 'submitted.json').exists():
            previous = json.loads((target / 'submitted.json').read_text())
            if previous['request_sha256'] != hashlib.sha256(raw).hexdigest():
                raise ValueError('Job ID already belongs to another request')
            return {'job_id': job_id, 'submitted': True}
        request = json.loads(raw)
        if (request.get('job_id') != job_id or not isinstance(request.get('code'), str)
                or request.get('timeout', 0) <= 0 or request.get('max_log_bytes', 0) <= 0):
            raise ValueError('Invalid process request')
        (target / 'input.py').write_text(request['code'], encoding='utf-8')
        (target / 'run.py').write_text(
            'import sys\nfrom pathlib import Path\n' + f'sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n' +
            'from paperagent_runtime import run_cell\n'
            f'_value = run_cell(Path({str(target / "input.py")!r}).read_text(encoding="utf-8"), '
            f'{job_id!r}, {{"__name__": "__main__", "__file__": {str(target / "input.py")!r}}}, {str(Path(root).resolve())!r})\n'
            'if _value is not None: print(repr(_value), flush=True)\n', encoding='utf-8')
        (target / 'stdout.log').touch()
        (target / 'stderr.log').touch()
        atomic_json(target / 'spec.json', request)
        atomic_json(target / 'submitted.json', {'job_id': job_id, 'submitted_at': time.time(),
                                               'request_sha256': hashlib.sha256(raw).hexdigest()})
        try:
            subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), 'supervise',
                              str(Path(root).resolve()), job_id, str(lock.fileno())],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True, pass_fds=(lock.fileno(),))
        except BaseException:
            # Submission is never silently replayed, even if spawning failed.
            atomic_json(target / 'done.json', {'job_id': job_id, 'exit_code': 127,
                        'duration_seconds': 0, 'error': traceback.format_exc(), 'stop_reason': None})
        return {'job_id': job_id, 'submitted': True}


def _signal(group, value):
    try:
        os.killpg(group, value)
    except ProcessLookupError:
        pass


def _exited(proc):
    # Linux production: observe exit without reaping the leader, so its PGID
    # cannot be recycled before the supervisor terminates remaining children.
    if sys.platform == 'linux' and hasattr(os, 'waitid') and hasattr(os, 'WNOWAIT'):
        return os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    return proc.poll() is not None


def supervise(root, job_id, lock_fd):
    # Keep the inherited lock descriptor open until the receipt is committed.
    target = directory(root, job_id)
    request = json.loads((target / 'spec.json').read_text())
    started = time.monotonic()
    proc = None
    reason = None
    sizes = {'stdout': 0, 'stderr': 0}
    error = None
    try:
        proc = subprocess.Popen([sys.executable, '-u', str(target / 'run.py')], cwd=root,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True,
                                env={**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8'})
        atomic_json(target / 'process.json', {'job_id': job_id, 'pid': proc.pid, 'supervisor_pid': os.getpid()})
        selector = selectors.DefaultSelector()
        logs = {name: (target / (name + '.log')).open('ab', buffering=0) for name in sizes}
        for name, pipe in (('stdout', proc.stdout), ('stderr', proc.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        stopped_at = None
        sent = 0
        exited_at = None
        try:
            while True:
                now = time.monotonic()
                finished = _exited(proc)
                if reason is None and not finished:
                    if (target / 'stop.json').exists():
                        reason = json.loads((target / 'stop.json').read_text()).get('reason') or 'cancelled'
                    elif now - started >= request['timeout']:
                        reason = 'timeout'
                if reason and stopped_at is None:
                    stopped_at = now
                if stopped_at is not None:
                    # Interrupt -> terminate -> kill only this process group.
                    elapsed = now - stopped_at
                    level = 3 if elapsed >= 5 else 2 if elapsed >= 3 else 1
                    if level > sent:
                        _signal(proc.pid, {1: signal.SIGINT, 2: signal.SIGTERM, 3: signal.SIGKILL}[level])
                        sent = level
                for key, _ in selector.select(.05):
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    remaining = max(0, request['max_log_bytes'] - sum(sizes.values()))
                    data = chunk[:remaining]
                    logs[key.data].write(data)
                    sizes[key.data] += len(data)
                    if len(data) != len(chunk) and reason is None:
                        reason = 'output_limit'
                if finished:
                    if exited_at is None:
                        exited_at = now
                        # Background descendants cannot outlive a completed job
                        # and mutate files after its snapshot is frozen.
                        if proc.returncode is None:
                            _signal(proc.pid, signal.SIGKILL)
                    if not selector.get_map():
                        break
                    if now - exited_at > 2:
                        raise RuntimeError('Process output pipes did not close after termination')
        finally:
            selector.close()
            for stream in logs.values():
                os.fsync(stream.fileno())
                stream.close()
    except BaseException:
        error = traceback.format_exc()
    finally:
        if proc is not None:
            if proc.returncode is None:
                _signal(proc.pid, signal.SIGKILL)
            proc.wait()
        atomic_json(target / 'done.json', {'job_id': job_id, 'exit_code': proc.returncode if proc else 127,
                    'duration_seconds': time.monotonic() - started, 'stop_reason': reason,
                    'error': error, 'log_bytes': sizes})
        os.close(lock_fd)


def status(root, job_id, offsets=None, chunk_bytes=64 * 1024):
    target = directory(root, job_id)
    offsets = offsets or {}
    # Read the completion marker first. If present, the logs are already closed.
    done = json.loads((target / 'done.json').read_text()) if (target / 'done.json').exists() else None
    logs = {}
    for name in ('stdout', 'stderr'):
        source = target / (name + '.log')
        offset = int(offsets.get(name, 0))
        if offset < 0:
            raise ValueError('Invalid log offset')
        raw = b''
        size = source.stat().st_size if source.exists() else 0
        if size < offset:
            raise ValueError('Process log was truncated')
        if source.exists():
            with source.open('rb') as stream:
                stream.seek(offset)
                raw = stream.read(min(chunk_bytes, 64 * 1024, size - offset))
        logs[name] = {'data': base64.b64encode(raw).decode('ascii'), 'offset': offset + len(raw), 'size': size}
    return {'job_id': job_id, 'submitted': (target / 'submitted.json').exists(), 'done': done, 'logs': logs}


def stop(root, job_id, reason):
    target = directory(root, job_id)
    if not (target / 'done.json').exists() and not (target / 'stop.json').exists():
        atomic_json(target / 'stop.json', {'reason': reason})
    return status(root, job_id)


if __name__ == '__main__':
    operation, root, job_id, *arguments = sys.argv[1:]
    if operation == 'supervise':
        supervise(root, job_id, int(arguments[0]))
    else:
        if operation == 'submit':
            result = submit(root, job_id)
        elif operation == 'status':
            result = status(root, job_id, json.loads(arguments[0]) if arguments else None)
        elif operation == 'stop':
            result = stop(root, job_id, arguments[0] if arguments else 'cancelled')
        else:
            raise ValueError('Unknown job operation')
        print(json.dumps(result))
