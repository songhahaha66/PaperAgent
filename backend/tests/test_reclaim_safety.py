"""Cross-process exclusion and completed-but-undelivered job reclamation."""
import asyncio
import json
import subprocess
import sys
import time

import pytest

from ai_system.sandbox import reclaim
from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
from ai_system.sandbox.remote_kernel import RemoteKernel
from ai_system.sandbox.settings import SandboxSettings
from helpers.process_jobs import ProcessJobs
from test_sandbox_reclaim import Directory, _runtime, _write_runs, _view, settings, reset_reclaim_state


def test_other_process_execution_lock_protects_old_job(tmp_path, settings):
    from services.file_services.workspace_lock import _open
    runtime, session = _runtime(tmp_path, settings)
    with _open(runtime.workspace, runtime=True) as stream:
        path = stream.name
    child = subprocess.Popen([sys.executable, '-c',
        'import fcntl,sys; f=open(sys.argv[1],"a+b"); fcntl.flock(f,fcntl.LOCK_EX); '
        'print("locked",flush=True); sys.stdin.read()', path], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert child.stdout.readline().strip() == b'locked'
        directory = Directory([_view('sb-old', cache_key=session.cache_key, created_at=0)])
        assert asyncio.run(reclaim.reclaim_project('proj-1', 'paused', directory=directory,
                                                  settings=settings, now=time.time()+5000)) == []
        assert not directory.killed
    finally:
        child.communicate(timeout=5)
    assert asyncio.run(reclaim.reclaim_project('proj-1', 'paused', directory=directory,
                                              settings=settings)) == [('sb-old', 'paused')]


@pytest.mark.parametrize('remote_state', [True, None, ConnectionError('offline')])
def test_remote_running_or_unknown_never_ages_into_reclaim(tmp_path, settings, monkeypatch, remote_state):
    runtime, session = _runtime(tmp_path, settings)
    manifest = _write_runs(runtime, {'job': {'status':'running', 'execution_mode':'process',
        'submission_state':'unknown', 'phase_started_at':0}})
    async def status(run_id):
        if isinstance(remote_state, Exception):
            raise remote_state
        return remote_state
    session.job_status = status
    monkeypatch.setattr('ai_system.sandbox.factory.create_session', lambda *a, **kw: session)
    directory = Directory([_view('sb-old', created_at=0)])
    assert asyncio.run(reclaim.reclaim_project('proj-1', 'paused', directory=directory,
        settings=settings, now=time.time()+100000)) == []
    assert not directory.killed
    assert json.loads(manifest.read_text())['runs']['job']['status'] == 'running'


def test_failed_delivery_is_retained_and_deadline_does_not_slide(tmp_path, settings, monkeypatch):
    runtime, session = _runtime(tmp_path, settings)
    manifest = _write_runs(runtime, {'job': {'status':'success', 'execution_mode':'process',
        'submission_state':'acknowledged', 'delivery_state':'pending', 'process_receipt':{'job_id':'job'}}})
    monkeypatch.setattr('ai_system.sandbox.factory.create_session', lambda *a, **kw: session)
    async def fail(*args):
        raise ConnectionError('delivery failed')
    monkeypatch.setattr(reclaim, '_recover_delivery', fail)
    renewed=[]
    async def renew(seconds): renewed.append(seconds)
    session.renew=renew
    directory=Directory([_view('sb-old', created_at=0)])
    async def sweep(now):
        return await reclaim.reclaim_project('proj-1', 'paused', directory=directory, settings=settings, now=now)
    assert asyncio.run(sweep(10000)) == []
    deadline=json.loads(manifest.read_text())['runs']['job']['recovery_deadline']
    assert renewed
    renewed.clear()
    assert asyncio.run(sweep(deadline+1)) == []
    row=json.loads(manifest.read_text())['runs']['job']
    assert row['delivery_state']=='pending' and row['recovery_deadline']==deadline
    assert not directory.killed and not renewed


@pytest.mark.parametrize('receipt_saved', [True, False])
def test_reclaim_recovers_original_completed_job_and_files(tmp_path, monkeypatch, receipt_saved):
    async def check():
        settings=SandboxSettings(provider='fake')
        first=ProcessJobs(tmp_path/'backend',tmp_path/'server',settings=settings)
        first.work_id='proj-1'
        kernel=RemoteKernel(first.backend_root,first,settings,timeout=10)
        first.fail_download=True
        result=await kernel.execute("from pathlib import Path\nprint('original log')\nPath('code/recovered.py').write_text('answer = 42\\n')")
        assert result.delivery_error and kernel.pending_run
        if not receipt_saved:
            row=kernel.fs.get_run_info(result.run_id)
            row.pop('process_receipt')
            row.update(status='running',submission_state='unknown',compute_state='unknown')
            kernel.fs._save_manifest()
            (kernel.fs.run_dir(result.run_id)/'stdout.log').write_text('partial')
        reclaim._LIVE.clear()  # New backend has no in-memory executor registry.
        second=ProcessJobs(first.backend_root,tmp_path/'server',settings=settings)
        second.work_id='proj-1'
        second.job_status=OpenSandboxSession.job_status.__get__(second)
        monkeypatch.setattr('ai_system.sandbox.factory.create_session',lambda *a,**kw: second)
        directory=Directory([_view(first.id,created_at=0,cache_key=first.cache_key)])
        released=await reclaim.reclaim_project('proj-1','paused',directory=directory,settings=settings)
        assert released
        assert (first.backend_root/'code/recovered.py').read_text()=='answer = 42\n'
        row=json.loads(kernel.fs._manifest_path.read_text())['runs'][result.run_id]
        assert row['delivery_state']=='available'
        assert not any(op=='submit' for op,_ in second.operations)
        assert (kernel.fs.run_dir(result.run_id)/'stdout.log').read_text().count('original log')==1
    asyncio.run(check())


def test_status_probe_does_not_treat_missing_receipt_as_completed():
    async def check():
        session=object.__new__(OpenSandboxSession)
        for value, expected in [({'submitted':True,'done':None},True),({'submitted':False,'done':None},None),
            ({'done':{'job_id':'job','exit_code':0}},False)]:
            async def rpc(*args): return value
            session._job_rpc=rpc
            assert await session.job_status('job') is expected
    asyncio.run(check())


def test_listing_snapshot_is_rechecked_after_lock(tmp_path, settings, monkeypatch):
    from contextlib import contextmanager
    from services.file_services.workspace_lock import try_execution_lock
    runtime, session = _runtime(tmp_path, settings)
    now=time.time()
    @contextmanager
    def updated_before_lock(workspace):
        runtime.save(last_job_at=now)
        with try_execution_lock(workspace) as acquired:
            yield acquired
    monkeypatch.setattr('services.file_services.workspace_lock.try_execution_lock', updated_before_lock)
    directory=Directory([_view('sb-old',created_at=0)])
    assert asyncio.run(reclaim.sweep_once(directory=directory, projects={'proj-1':'in_progress'},
        settings=settings, now=now)) == []
    assert not directory.killed


def test_startup_does_not_pause_another_backend_project(monkeypatch):
    import importlib.util
    from pathlib import Path
    from unittest.mock import MagicMock, AsyncMock
    import alembic.command
    import services.migrate_data
    import ai_system.config.logging_config
    monkeypatch.setattr(ai_system.config.logging_config, 'setup_simple_logging', lambda *a: None)
    monkeypatch.setattr(alembic.command, 'upgrade', lambda *a: None)
    monkeypatch.setattr(services.migrate_data, 'migrate_all', lambda: None)
    spec=importlib.util.spec_from_file_location('reclaim_startup_test',Path(__file__).parents[1]/'main.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db=MagicMock()
    monkeypatch.setattr(module,'Session',db,raising=False)
    monkeypatch.setattr(reclaim,'sweep_once',AsyncMock(return_value=[]))
    async def check():
        lifecycle=module.lifespan(module.app)
        await anext(lifecycle)
        await lifecycle.aclose()
    asyncio.run(check())
    db.assert_not_called()
