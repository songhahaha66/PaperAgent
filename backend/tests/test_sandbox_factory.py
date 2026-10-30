"""Provider switching through both entry points, without a vendor or network."""
import asyncio
import base64
import json
import shlex
from dataclasses import replace

import pytest

from ai_system.sandbox import factory
from ai_system.sandbox.commands import run_command, sandbox_operation
from ai_system.sandbox.contracts import ExecutionOutcome
from ai_system.sandbox.settings import SandboxSettings
from helpers.remote_files import RemoteFiles
from services.file_services.workspace_layout import workspace_path


@pytest.fixture
def provider(tmp_path, monkeypatch):
    # Registration is process-wide at startup; keep each test independent.
    monkeypatch.setattr(factory, '_PROVIDERS', dict(factory._PROVIDERS))
    settings = SandboxSettings(provider='test-rpc', endpoint='rpc+test://execution', api_key='')
    sessions = []

    class Session(RemoteFiles):
        async def execute(self, code, run_id, output):
            self.calls.append(code)
            await output('stdout', 'research completed\n')
            (self.remote / 'outputs/result.csv').write_text('value\n42\n')
            return ExecutionOutcome()

        async def exec(self, argv, *, stdin=None, timeout=180):
            self.commands.append((list(argv), stdin, timeout))
            (self.remote / '.build/latex/paper.pdf').write_bytes(b'frozen compiler output')
            return 0, 'compile completed', ''

    def create(config, *, workspace_dir, work_id=None, experiment_id=None, cache_key=None):
        session = Session(workspace_dir, tmp_path / 'remote', settings=config, cache_key=cache_key)
        if config.provider == 'another':
            session.root = '/different/workspace'
        session.commands = []
        session.request_ids = (work_id, experiment_id)
        sessions.append(session)
        return session

    factory.register_provider(settings.provider, create)
    monkeypatch.setattr(SandboxSettings, 'from_env', classmethod(lambda cls: settings))
    return settings, sessions, create


@sandbox_operation(input_selector=lambda root: ['paper.tex'])
async def compile_fixture(root):
    return await run_command(
        ['xelatex', str(root / 'paper.tex'), 'literal ; $(echo ignored)'], root,
        stdin='中文\n$(literal)\n', timeout=7)


def test_registered_provider_handles_research_and_compilation(tmp_path, provider, monkeypatch):
    settings, sessions, _ = provider
    root = tmp_path / 'project'
    # No OpenSandbox constructor or server-host mount is needed by this adapter.
    def unexpected(*args, **kwargs):
        raise AssertionError('Vendor implementation leaked into the shared caller')
    monkeypatch.setattr('ai_system.sandbox.opensandbox_provider.OpenSandboxSession', unexpected)

    async def check():
        kernel = factory.create_kernel(root, settings=settings, work_id='work', experiment_id='experiment')
        try:
            result = await kernel.execute('research cell')
            assert result.ok and result.delivery_error is None
            assert (root / 'outputs/result.csv').read_text() == 'value\n42\n'
        finally:
            await kernel.close()
        assert sessions[0].request_ids == ('work', 'experiment')
        (root / 'paper.tex').write_text('trusted fixture source')
        assert await compile_fixture(root) == (0, 'compile completed', '')
        assert sessions[1].commands == [(
            ['xelatex', '/workspace/paper.tex', 'literal ; $(echo ignored)'],
            '中文\n$(literal)\n', 7)]
        assert (root / '.build/latex/paper.pdf').read_bytes() == b'frozen compiler output'
        # Recreate the compiler session and retrieve the original frozen bytes.
        (sessions[1].remote / '.build/latex/paper.pdf').write_bytes(b'changed scratch')
        await compile_fixture(root)
        assert sessions[2].commands == []
        assert (root / '.build/latex/paper.pdf').read_bytes() == b'frozen compiler output'
        assert all(session.closed for session in sessions)

    asyncio.run(check())


def test_pending_research_delivery_cannot_switch_providers(tmp_path, provider):
    settings, sessions, create = provider
    factory.register_provider('another', create)

    async def check():
        kernel = factory.create_kernel(tmp_path / 'project', settings=settings)
        kernel.session.fail_download = True
        result = await kernel.execute('one execution')
        assert result.ok and result.delivery_error
        await kernel.close()
        rebuilt = factory.create_kernel(tmp_path / 'project', settings=replace(settings, provider='another'))
        try:
            with pytest.raises(RuntimeError, match='另一沙箱平台'):
                await rebuilt.retry_outputs()
            assert not sessions[-1].started and sessions[-1].calls == []
        finally:
            await rebuilt.close()

    asyncio.run(check())


def test_compiler_receipts_cannot_switch_providers(tmp_path, provider, monkeypatch):
    settings, sessions, create = provider
    factory.register_provider('another', create)
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'paper.tex').write_text('source')

    async def check():
        await compile_fixture(root)
        monkeypatch.setattr(SandboxSettings, 'from_env', classmethod(lambda cls: replace(settings, provider='another')))
        with pytest.raises(RuntimeError, match='另一沙箱平台'):
            await compile_fixture(root)
        assert sessions[-1].commands == [] and sessions[-1].closed

    asyncio.run(check())


def test_legacy_opensandbox_compiler_receipts_remain_recoverable(tmp_path, provider, monkeypatch):
    settings, sessions, create = provider
    monkeypatch.setitem(factory._PROVIDERS, 'opensandbox', create)
    monkeypatch.setattr(SandboxSettings, 'from_env', classmethod(lambda cls: replace(settings, provider='opensandbox')))
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'paper.tex').write_text('source')

    async def check():
        await compile_fixture(root)
        records = list(workspace_path(root, '.system/compiler-receipts').rglob('*.json'))
        assert records
        for path in records:
            record = json.loads(path.read_text())
            record.pop('provider')
            path.write_text(json.dumps(record))
        await compile_fixture(root)
        assert sessions[-1].commands == []

    asyncio.run(check())


@pytest.mark.parametrize('name', ['unknown', 'local'])
def test_both_entry_points_reject_unsupported_provider(tmp_path, monkeypatch, name):
    settings = SandboxSettings(provider=name)
    monkeypatch.setattr(SandboxSettings, 'from_env', classmethod(lambda cls: settings))
    (tmp_path / 'paper.tex').write_text('source')
    with pytest.raises(ValueError):
        factory.create_kernel(tmp_path, settings=settings)
    with pytest.raises(ValueError):
        asyncio.run(compile_fixture(tmp_path))


def test_provider_registration_does_not_replace_live_adapters(provider):
    settings, _, create = provider
    with pytest.raises(ValueError, match='已注册'):
        factory.register_provider(settings.provider, create)
    with pytest.raises(ValueError, match='本机'):
        factory.register_provider('local', create)


def test_opensandbox_preserves_literal_arguments_and_stdin(tmp_path):
    from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
    session = OpenSandboxSession(SandboxSettings(api_key='test', host_workspace_root='/server/cache'), workspace_dir=tmp_path)
    argv = ['pandoc', 'file with spaces', "quote'", '$(touch ignored);', 'line\nbreak', '']
    stdin = '中文\n\'$literal $(ignored)\n'
    calls = []

    async def command(text, timeout):
        calls.append((text, timeout))
        return 17, 'stdout', 'stderr'
    session.command = command

    async def check():
        assert await session.exec(argv, stdin=stdin, timeout=11) == (17, 'stdout', 'stderr')
        prefix, command_text = calls[0][0].split(' | base64 -d | ', 1)
        assert shlex.split(command_text) == argv
        assert base64.b64decode(shlex.split(prefix)[2]).decode() == stdin
        assert calls[0][1] == 11
        await session.exec(argv)
        assert shlex.split(calls[1][0]) == argv
        with pytest.raises(ValueError):
            await session.exec('pandoc unsafe shell string')

    asyncio.run(check())


@pytest.mark.parametrize('exit_code,error,known',[(1,'exit status 1',True),(0,None,True),(None,'stream lost',False),(0,'runtime failed',False)])
def test_compiler_nonzero_exit_is_a_completed_command(tmp_path,exit_code,error,known):
    from types import SimpleNamespace
    from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
    async def run(command,*,opts,handlers):
        await handlers.on_stdout(SimpleNamespace(text='paper.tex:12: Undefined control sequence.'))
        return SimpleNamespace(exit_code=exit_code,error=SimpleNamespace(name='CommandError',value=error,traceback=[]) if error else None)
    async def check():
        session=OpenSandboxSession(SandboxSettings(api_key='test',host_workspace_root='/server/cache'),workspace_dir=tmp_path)
        session.sandbox=SimpleNamespace(commands=SimpleNamespace(run=run))
        if known:
            code,out,err=await session.command('xelatex paper.tex',10)
            assert code==exit_code and 'paper.tex:12:' in out
        else:
            with pytest.raises(RuntimeError,match='回执'):await session.command('xelatex paper.tex',10)
    asyncio.run(check())


@pytest.mark.parametrize('endpoint,key', [
    ('http://remote.example', 'test'), ('https://name:secret@example.com', 'test'),
    ('https://example.com/path', 'test'), ('https://example.com', ''),
])
def test_opensandbox_validates_connection_before_start(tmp_path, endpoint, key):
    with pytest.raises(ValueError):
        factory.create_session(tmp_path, settings=SandboxSettings(
            endpoint=endpoint, api_key=key, host_workspace_root='/server/cache'))


def test_environment_does_not_impose_opensandbox_transport_on_other_providers(monkeypatch):
    monkeypatch.delenv('PAPERAGENT_SANDBOX_CONFIG', raising=False)
    monkeypatch.setenv('PAPERAGENT_SANDBOX_PROVIDER', 'test-rpc')
    monkeypatch.setenv('PAPERAGENT_SANDBOX_ENDPOINT', 'rpc+test://execution')
    monkeypatch.setenv('PAPERAGENT_SANDBOX_API_KEY', '')
    assert SandboxSettings.from_env().endpoint == 'rpc+test://execution'
