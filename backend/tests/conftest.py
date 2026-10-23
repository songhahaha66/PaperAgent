"""Offline boundaries shared by integration tests, with isolated backend state."""
import os
import tempfile

# Apply before test imports create clients or load the application's .env.
os.environ['LANGSMITH_TRACING'] = 'false'
os.environ['LANGCHAIN_TRACING_V2'] = 'false'
# Subprocess kernels must not rebuild fonts for every cell in a read-only HOME.
for name, prefix in [('MPLCONFIGDIR', 'paperagent-mpl-'), ('XDG_CACHE_HOME', 'paperagent-cache-')]:
    if name not in os.environ:
        os.environ[name] = tempfile.mkdtemp(prefix=prefix)

import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path_factory, monkeypatch):
    monkeypatch.setenv('PA_DATA_PATH', str(tmp_path_factory.mktemp('backend-data')))


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    import httpx
    def blocked(*args, **kwargs):
        raise AssertionError('Offline tests must inject an HTTP transport or sandbox session')
    async def blocked_async(*args, **kwargs):
        blocked()
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', blocked_async)


@pytest.fixture
def offline_kernel(monkeypatch):
    """Explicit trusted subprocess execution; never enables local app fallback."""
    from ai_system.sandbox.kernel import PythonKernel
    from ai_system.sandbox.evidence import freeze_inputs, seal_run
    class TestKernel(PythonKernel):
        async def execute(self, code, *, on_event=None):
            async def event(item):
                if item['type'] == 'running':
                    freeze_inputs(self.fs, item['run_id'])
                if item['type'] == 'finished' and item['status'] in {'success', 'error'}:
                    seal_run(self.fs, item['run_id'])
                if on_event:
                    await on_event(item)
            return await super().execute(code, on_event=event)
    def create(workspace_dir, *, timeout=180, fs=None, **kwargs):
        return TestKernel(workspace_dir, timeout=timeout, fs=fs)
    monkeypatch.setattr('ai_system.graph.context.create_kernel', create)


@pytest.fixture
def offline_compiler(monkeypatch, tmp_path_factory):
    """Run trusted compiler fixtures on a separate tree through the file API fake."""
    import asyncio
    from helpers.remote_files import RemoteFiles
    from ai_system.sandbox.settings import SandboxSettings
    import ai_system.sandbox.factory as factory
    remote_root = tmp_path_factory.mktemp('compiler-server')
    settings = SandboxSettings(api_key='offline', host_workspace_root='/server-only/cache')
    class Compiler(RemoteFiles):
        async def exec(self, argv, *, stdin=None, timeout=180):
            process = await asyncio.create_subprocess_exec(
                *(arg.replace('/workspace', str(self.remote)) for arg in argv), cwd=self.remote,
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(process.communicate(stdin.encode() if stdin is not None else None), timeout)
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            return process.returncode, out.decode(errors='replace'), err.decode(errors='replace')
    def create(settings, *, workspace_dir, cache_key=None, **kwargs):
        return Compiler(workspace_dir, remote_root, cache_key=cache_key, settings=settings)
    monkeypatch.setitem(factory._PROVIDERS, 'opensandbox', create)
    monkeypatch.setattr(SandboxSettings, 'from_env', classmethod(lambda cls: settings))
