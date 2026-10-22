"""Compilation shares the persistent-volume and project-lock boundary with Python."""
import asyncio
import base64
import shlex
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from .opensandbox_provider import OpenSandboxSession
from .settings import SandboxSettings
from services.file_services.workspace_lock import execution_lock

_ACTIVE = ContextVar('sandbox_compiler', default=None)


def sandbox_operation(function):
    @wraps(function)
    async def wrapped(workspace, *args, **kwargs):
        workspace = Path(workspace).absolute()
        if _ACTIVE.get() is not None:
            return await function(workspace, *args, **kwargs)
        async with execution_lock(workspace):
            settings = SandboxSettings.from_env()
            if settings.provider != 'opensandbox':
                raise RuntimeError('编译必须使用 OpenSandbox，不会回退后端执行')
            session = OpenSandboxSession(settings, workspace_dir=workspace)
            token = None
            try:
                await asyncio.wait_for(session.start(), settings.startup_timeout)
                token = _ACTIVE.set((session, workspace))
                return await function(workspace, *args, **kwargs)
            finally:
                if token is not None:
                    _ACTIVE.reset(token)
                await session.close()
    return wrapped


async def run_command(cmd, cwd, stdin=None, timeout=180):
    if _ACTIVE.get() is None:
        @sandbox_operation
        async def once(workspace):
            return await run_command(cmd, workspace, stdin, timeout)
        return await once(cwd)
    session, root = _ACTIVE.get()
    def argument(value):
        value = str(value)
        if str(root) in value:
            value = value.replace(str(root), '/workspace')
        return value
    command = shlex.join([argument(value) for value in cmd])
    if stdin is not None:
        encoded = base64.b64encode(stdin.encode()).decode('ascii')
        command = 'printf %s ' + shlex.quote(encoded) + ' | base64 -d | ' + command
    from services.file_services.file_helper import atomic_write
    from services.file_services.workspace_layout import safe_join
    import uuid
    receipt = safe_join(root, '.build/commands/' + uuid.uuid4().hex + '.log')
    try:
        result = await asyncio.wait_for(session.command(command, timeout), timeout + 15)
        atomic_write(receipt, lambda f: f.write((f'exit_code={result[0]}\n' + result[1] + '\n' + result[2]).encode()))
        return result
    except BaseException as exc:
        detail = 'incomplete: ' + type(exc).__name__ + '\n' + str(exc) + '\n' + getattr(exc, 'sandbox_stdout', '') + '\n' + getattr(exc, 'sandbox_stderr', '')
        if session.settings.api_key:
            detail = detail.replace(session.settings.api_key, '[redacted]')
        atomic_write(receipt, lambda f: f.write(detail.encode()))
        if isinstance(exc, asyncio.CancelledError):
            raise
        safe = RuntimeError(str(exc).replace(session.settings.api_key, '[redacted]') if session.settings.api_key else str(exc))
        safe.sandbox_stdout = getattr(exc, 'sandbox_stdout', '')
        safe.sandbox_stderr = getattr(exc, 'sandbox_stderr', '')
        raise safe from None
