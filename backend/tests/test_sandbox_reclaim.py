"""Idle, immediate and orphan reclamation. The sandbox client is a fake."""
import asyncio
import json
import logging
import time

import pytest

from ai_system.sandbox.reclaim import (
    SandboxView, decide, idle_ttl_seconds, reclaim_project, sandbox_metadata,
    sweep_interval_seconds, sweep_once,
)
from ai_system.sandbox.runtime_manager import RuntimeManager
from ai_system.sandbox.settings import SandboxSettings
from helpers.remote_files import RemoteFiles


class Directory:
    def __init__(self, views):
        self.views = list(views)
        self.killed = []

    async def list_sandboxes(self):
        return [view for view in self.views if view.id not in self.killed]

    async def kill(self, sandbox_id):
        if sandbox_id in self.killed:
            error = RuntimeError('sandbox not found')
            error.status_code = 404
            raise error
        self.killed.append(sandbox_id)


class Track(RemoteFiles):
    def __init__(self, backend_root, server_root=None, *, cache_key=None, settings=None):
        super().__init__(backend_root, server_root, cache_key=cache_key, settings=settings)
        self.events = []

    async def start(self):
        self.events.append('start')
        await super().start()

    async def attach(self, handle):
        self.events.append(('attach', handle.get('sandbox_id')))
        await super().attach(handle)

    async def close(self):
        self.events.append('close')
        await super().close()


@pytest.fixture(autouse=True)
def reset_reclaim_state():
    from ai_system.sandbox import reclaim
    reclaim._BUSY_KEYS.clear()
    reclaim._BUSY_COUNTS.clear()
    reclaim._RECLAIMING.clear()
    reclaim._RECLAIMING_KEYS.clear()
    reclaim._LIVE.clear()
    reclaim._ACTIVE_RUNS.clear()
    yield
    reclaim._BUSY_KEYS.clear()
    reclaim._BUSY_COUNTS.clear()
    reclaim._RECLAIMING.clear()
    reclaim._RECLAIMING_KEYS.clear()
    reclaim._LIVE.clear()
    reclaim._ACTIVE_RUNS.clear()


@pytest.fixture
def settings():
    return SandboxSettings(provider='fake', api_key='test', endpoint='http://127.0.0.1:9',
                           host_workspace_root='/cache', lifetime=1800, max_timeout=3600)


def test_idle_ttl_and_sweep_interval_are_configurable():
    assert idle_ttl_seconds({}) == 900
    assert idle_ttl_seconds({'PAPERAGENT_SANDBOX_IDLE_TTL_SECONDS': '120'}) == 120
    assert idle_ttl_seconds({'PAPERAGENT_SANDBOX_IDLE_TTL_SECONDS': '0'}) == 900
    assert idle_ttl_seconds({'PAPERAGENT_SANDBOX_IDLE_TTL_SECONDS': 'nope'}) == 900
    assert sweep_interval_seconds({}) == 180
    assert sweep_interval_seconds({'PAPERAGENT_SANDBOX_SWEEP_SECONDS': '45'}) == 45


def test_metadata_marks_research_and_run_sandboxes():
    research = sandbox_metadata(work_id='proj-1', experiment_id='research', cache_key='abc')
    assert research == {'application': 'paperagent', 'role': 'research', 'cache_key': 'abc', 'work_id': 'proj-1'}
    compiler = sandbox_metadata(work_id=None, experiment_id='compiler', cache_key='def')
    assert compiler['role'] == 'run' and 'work_id' not in compiler


def test_metadata_values_are_kubernetes_labels():
    import re
    from ai_system.sandbox.reclaim import label_value
    digest = '484ceb8e' + ('ab' * 26) + '4342'
    assert len(digest) == 64
    label = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?$')
    samples = [
        sandbox_metadata(work_id='proj-1', experiment_id='research', cache_key=digest),
        sandbox_metadata(work_id='项目 id/with spaces', experiment_id='compiler', cache_key=digest),
        sandbox_metadata(work_id='a' * 80, experiment_id='task-1', cache_key='not-a-digest'),
    ]
    assert samples[0]['cache_key'] == digest[:32]
    assert samples[0]['cache_key'] == label_value(digest)
    for meta in samples:
        assert meta
        for key, value in meta.items():
            assert isinstance(value, str) and len(value) <= 63 and label.fullmatch(value), (key, value)


def _view(sandbox_id, *, role='research', work_id='proj-1', created_at=0, cache_key='cache'):
    meta = {'application': 'paperagent', 'role': role, 'cache_key': cache_key}
    if work_id:
        meta['work_id'] = work_id
    return SandboxView(sandbox_id, meta, created_at=created_at)


def test_decide_keeps_fresh_work_and_names_every_reclaim_reason():
    now, ttl = 10_000, 900
    fresh = _view('fresh', created_at=now - 10)
    idle = _view('idle', created_at=now - 5000)
    projects = {'proj-1': 'in_progress', 'paused-proj': 'paused', 'done-proj': 'completed', 'failed-proj': 'failed'}
    assert decide(fresh, projects=projects, activity={}, now=now, ttl=ttl) is None
    assert decide(idle, projects=projects, activity={}, now=now, ttl=ttl) == 'idle'
    assert decide(_view('paused', work_id='paused-proj', created_at=now), projects=projects, activity={}, now=now, ttl=ttl) == 'paused'
    assert decide(_view('done', work_id='done-proj', created_at=now), projects=projects, activity={}, now=now, ttl=ttl) == 'completed'
    assert decide(_view('missing', work_id='gone', created_at=now), projects=projects, activity={}, now=now, ttl=ttl) == 'deleted'
    assert decide(_view('failed', work_id='failed-proj', created_at=now), projects=projects, activity={}, now=now, ttl=ttl) == 'inactive'
    assert decide(_view('temp', role='run', work_id='paused-proj', created_at=now), projects=projects, activity={}, now=now, ttl=ttl) is None
    assert decide(_view('temp-old', role='run', work_id='paused-proj', created_at=now - 5000), projects=projects, activity={}, now=now, ttl=ttl) == 'idle'
    assert decide(_view('orphan', work_id=None, created_at=now - 5000), projects=projects, activity={}, now=now, ttl=ttl) == 'orphan'
    assert decide(_view('young-orphan', work_id=None, created_at=now), projects=projects, activity={}, now=now, ttl=ttl) is None
    # A recent job resets the idle clock even when the sandbox itself is old.
    assert decide(idle, projects=projects, activity={'last_job_at': now - 5}, now=now, ttl=ttl) is None
    assert decide(fresh, projects=None, activity={}, now=now, ttl=ttl) is None
    assert decide(idle, projects=None, activity={}, now=now, ttl=ttl) == 'idle'


def test_sweeper_deletes_orphans_and_idle_sandboxes_only(caplog):
    now = 50_000
    views = [
        _view('busy', created_at=now - 5000),
        _view('fresh', created_at=now - 30),
        _view('idle', created_at=now - 5000),
        _view('orphan', work_id='missing', created_at=now - 5),
        _view('paused', work_id='paused-proj', created_at=now),
        _view('compile', role='run', work_id='paused-proj', created_at=now, cache_key='compile-key'),
    ]
    directory = Directory(views)
    from ai_system.sandbox.reclaim import hold_sandbox
    hold_sandbox('busy')
    caplog.set_level(logging.INFO)

    async def run():
        try:
            first = await sweep_once(directory=directory, projects={'proj-1': 'in_progress', 'paused-proj': 'paused'},
                                     settings=object(), now=now, ttl=900)
            assert 'busy' not in directory.killed
        finally:
            from ai_system.sandbox.reclaim import release_sandbox
            release_sandbox('busy')
        second = await sweep_once(directory=directory, projects={'proj-1': 'in_progress', 'paused-proj': 'paused'},
                                  settings=object(), now=now, ttl=900)
        return first, second

    first, second = asyncio.run(run())
    assert dict(first) == {'idle': 'idle', 'orphan': 'deleted', 'paused': 'paused'}
    assert 'compile' not in directory.killed
    assert dict(second)['busy'] == 'idle'
    assert 'proj-1' in caplog.text and 'reason=idle' in caplog.text
    assert 'paused-proj' in caplog.text and 'reason=paused' in caplog.text
    assert 'missing' in caplog.text and 'reason=deleted' in caplog.text


@pytest.mark.parametrize('reason', ['paused', 'completed', 'deleted'])
def test_immediate_reclaim_skips_a_running_job(reason, caplog):
    now = 80_000
    view = _view('sb-run', created_at=now - 5000)
    directory = Directory([view])
    caplog.set_level(logging.INFO)

    async def run():
        from ai_system.sandbox.reclaim import hold_sandbox, release_sandbox
        hold_sandbox('sb-run')
        try:
            return await reclaim_project('proj-1', reason, directory=directory, settings=object(), now=now, ttl=900)
        finally:
            release_sandbox('sb-run')

    assert asyncio.run(run()) == []
    assert directory.killed == []
    assert 'project_id=proj-1' in caplog.text and 'sandbox_id=sb-run' in caplog.text
    assert '任务仍在运行' in caplog.text and f'reason={reason}' in caplog.text


def test_paused_completed_and_deleted_reclaim_without_waiting(caplog):
    now = 90_000
    caplog.set_level(logging.INFO)

    async def once(reason, sandbox_id, work_id='proj-1'):
        view = _view(sandbox_id, work_id=work_id, created_at=now)
        directory = Directory([view])
        released = await reclaim_project(work_id, reason, directory=directory, settings=object(), now=now, ttl=900)
        return released, directory.killed

    async def run():
        paused = await once('paused', 'sb-paused')
        completed = await once('completed', 'sb-done', 'proj-2')
        deleted = await once('deleted', 'sb-deleted', 'proj-3')
        return paused, completed, deleted

    paused, completed, deleted = asyncio.run(run())
    assert paused == ([('sb-paused', 'paused')], ['sb-paused'])
    assert completed == ([('sb-done', 'completed')], ['sb-done'])
    assert deleted == ([('sb-deleted', 'deleted')], ['sb-deleted'])
    for project, sandbox, reason in (
        ('proj-1', 'sb-paused', 'paused'),
        ('proj-2', 'sb-done', 'completed'),
        ('proj-3', 'sb-deleted', 'deleted'),
    ):
        assert f'project_id={project}' in caplog.text
        assert f'sandbox_id={sandbox}' in caplog.text
        assert f'reason={reason}' in caplog.text


def _runtime(tmp_path, settings, **extra):
    workspace = (tmp_path / 'project').resolve()
    workspace.mkdir()
    session = Track(workspace, settings=settings)
    runtime = RuntimeManager(workspace, session, settings)
    runtime.save(state='detached', sandbox_id='sb-old', context_id='ctx', expires_at=time.time() + 3600,
                 work_id='proj-1', role='research', last_job_at=time.time() - 5000,
                 workspace=str(runtime.workspace.absolute()), cache_key=session.cache_key,
                 deployment=runtime.deployment, **extra)
    return runtime, session


def _write_runs(runtime, runs):
    manifest = runtime.path.parents[2] / 'manifest.json'
    manifest.write_text(json.dumps({'runs': runs}))
    return manifest


def test_in_process_run_and_live_remote_job_are_not_reclaimed(tmp_path, settings, monkeypatch):
    runtime, session = _runtime(tmp_path, settings, active_run_ids=['run-1'])
    directory = Directory([_view('sb-old', created_at=0, cache_key=session.cache_key)])
    from ai_system.sandbox import reclaim as reclaim_mod
    reclaim_mod._ACTIVE_RUNS.add('run-1')

    async def run():
        released = await reclaim_project('proj-1', 'paused', directory=directory, settings=settings, ttl=900)
        assert released == [] and directory.killed == []
        assert json.loads(runtime.path.read_text())['sandbox_id'] == 'sb-old'
        reclaim_mod._ACTIVE_RUNS.clear()
        saved = json.loads(runtime.path.read_text())
        saved['active_run_ids'] = []
        runtime.path.write_text(json.dumps(saved))
        _write_runs(runtime, {'run-9': {
            'status': 'running', 'execution_mode': 'process', 'submission_state': 'unknown',
            'compute_state': 'running', 'process_receipt': None, 'phase_started_at': time.time()}})
        monkeypatch.setattr(reclaim_mod, 'remote_job_running', lambda handle, run_id, row: True)
        released = await reclaim_project('proj-1', 'deleted', directory=directory, settings=settings, ttl=900)
        assert released == [] and directory.killed == []
        assert json.loads(runtime.path.read_text())['sandbox_id'] == 'sb-old'

    asyncio.run(run())


def test_crash_before_submit_is_stale_and_reclaimed(tmp_path, settings):
    runtime, session = _runtime(tmp_path, settings)
    manifest = _write_runs(runtime, {'run-1': {
        'status': 'starting', 'submission_state': 'not_submitted', 'compute_state': 'not_started',
        'execution_mode': 'process', 'process_receipt': None}})
    directory = Directory([_view('sb-old', created_at=0, cache_key=session.cache_key)])

    async def run():
        released = await reclaim_project('proj-1', 'paused', directory=directory, settings=settings, ttl=900)
        assert released == [('sb-old', 'paused')]
        assert directory.killed == ['sb-old']

    asyncio.run(run())
    row = json.loads(manifest.read_text())['runs']['run-1']
    assert row['status'] == 'interrupted'
    assert row['failure_kind'] == 'preparation'
    assert row['submission_state'] == 'not_submitted'
    assert '未执行' in row['error']


def test_crash_after_submit_follows_the_remote_process(tmp_path, settings, monkeypatch):
    runtime, session = _runtime(tmp_path, settings, active_run_ids=['run-2'])
    stale = {
        'status': 'running', 'submission_state': 'unknown', 'compute_state': 'running',
        'execution_mode': 'process', 'process_receipt': None, 'phase_started_at': time.time() - 5000,
    }
    manifest = _write_runs(runtime, {'run-2': dict(stale)})
    directory = Directory([_view('sb-old', created_at=0, cache_key=session.cache_key)])
    from ai_system.sandbox import reclaim as reclaim_mod

    async def reclaim():
        return await reclaim_project('proj-1', 'paused', directory=directory, settings=settings, ttl=900)

    monkeypatch.setattr(reclaim_mod, 'remote_job_running', lambda handle, run_id, row: True)
    assert asyncio.run(reclaim()) == []
    assert directory.killed == []
    assert json.loads(manifest.read_text())['runs']['run-2']['status'] == 'running'

    monkeypatch.setattr(reclaim_mod, 'remote_job_running', lambda handle, run_id, row: False)
    assert asyncio.run(reclaim()) == [('sb-old', 'paused')]
    row = json.loads(manifest.read_text())['runs']['run-2']
    assert row['status'] == 'interrupted'
    assert row['failure_kind'] == 'transport_unknown'
    assert row['submission_state'] == 'unknown'
    assert json.loads(runtime.path.read_text())['active_run_ids'] == []


def test_short_cache_label_matches_a_held_sandbox():
    digest = 'a' * 64
    from ai_system.sandbox.reclaim import hold_key, release_key
    hold_key(digest)
    directory = Directory([_view('sb', created_at=0, cache_key=digest[:32])])

    async def run():
        try:
            return await sweep_once(directory=directory, projects={'proj-1': 'paused'}, settings=object(), now=10_000, ttl=900)
        finally:
            release_key(digest)

    assert asyncio.run(run()) == []
    assert directory.killed == []


def test_reclaim_clears_stale_id_and_the_next_start_creates(tmp_path, settings, monkeypatch, caplog):
    workspace = (tmp_path / 'project').resolve()
    workspace.mkdir()
    created = []

    def create(workspace_dir, *, settings=None, work_id=None, experiment_id=None, cache_key=None):
        session = Track(workspace_dir, settings=settings, cache_key=cache_key)
        session.work_id = work_id
        session.experiment_id = experiment_id
        created.append(session)
        return session

    monkeypatch.setattr('ai_system.sandbox.factory.create_session', create)
    session = Track(workspace, settings=settings)
    runtime = RuntimeManager(workspace, session, settings)
    runtime.save(state='detached', sandbox_id='sb-old', context_id='ctx-old', expires_at=time.time() + 3600,
                 work_id='proj-1', role='research', last_job_at=time.time(),
                 workspace=str(runtime.workspace.absolute()), cache_key=session.cache_key,
                 deployment=runtime.deployment, lease_id='lease-1')
    directory = Directory([_view('sb-old', created_at=0, cache_key=session.cache_key)])
    caplog.set_level(logging.INFO)

    async def run():
        released = await reclaim_project('proj-1', 'completed', directory=directory, settings=settings, ttl=900)
        saved = json.loads(runtime.path.read_text())
        assert released == [('sb-old', 'completed')]
        assert saved['state'] == 'destroyed'
        assert saved['sandbox_id'] is None
        assert saved['active_run_ids'] == []
        fresh = Track(workspace, settings=settings, cache_key=session.cache_key)
        manager = RuntimeManager(workspace, fresh, settings)
        await manager.start()
        assert fresh.events == ['start']
        assert manager.handle['sandbox_id'] not in {None, 'sb-old'}
        assert manager.session.id == manager.handle['sandbox_id']

    asyncio.run(run())
    assert created and created[0].events[0] == ('attach', 'sb-old')
    assert 'close' in created[0].events
    assert 'project_id=proj-1' in caplog.text and 'sandbox_id=sb-old' in caplog.text and 'reason=completed' in caplog.text
