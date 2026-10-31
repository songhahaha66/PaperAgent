"""Sandbox expiry, one rebuild, and readable errors. No live OpenSandbox server."""
import asyncio
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.remote_files import RemoteFiles
from ai_system.sandbox.contracts import ExecutionOutcome
from ai_system.sandbox.lifetime import (
    RETRIED_NOTE, SandboxLifetimeCap, SandboxUnavailable, granted_lifetime, renewal_seconds,
)
from ai_system.sandbox.remote_kernel import RemoteKernel
from ai_system.sandbox.settings import SandboxSettings


class SandboxApiException(Exception):
    def __init__(self, message='httpx.RemoteProtocolError: peer closed connection', status_code=502):
        super().__init__(message)
        self.status_code = status_code


class Expiring(RemoteFiles):
    def __init__(self, workspace, settings):
        super().__init__(workspace, settings=settings)
        self.renewals = []
        self.starts = 0
        self.fail_execute = 0
        self.fail_next_start = False
        self.renew_error = None
        self.renew_grant = None
        self.wipe_on_failure = False

    async def start(self):
        self.starts += 1
        if self.fail_next_start:
            self.fail_next_start = False
            raise RuntimeError('镜像拉取失败')
        await super().start()

    async def renew(self, seconds):
        self.renewals.append(seconds)
        if self.renew_error:
            raise self.renew_error
        grant = self.renew_grant if self.renew_grant is not None else seconds
        epoch = time.time() + grant
        self.expires_at = epoch
        return epoch

    async def execute(self, code, run_id, output):
        self.calls.append(code)
        if self.fail_execute:
            self.fail_execute -= 1
            if self.wipe_on_failure and self.remote.exists():
                shutil.rmtree(self.remote)
            raise SandboxApiException()
        await output('stdout', 'ok\n')
        (self.remote / 'outputs/table.csv').write_text(code)
        return ExecutionOutcome()


def kernel(tmp_path, settings=None, **kwargs):
    settings = settings or SandboxSettings(provider='fake', lifetime=1800, max_timeout=3600, startup_timeout=120)
    session = Expiring(tmp_path, settings)
    return RemoteKernel(tmp_path, session, settings, **kwargs), session


def test_local_expiry_matches_the_server_grant_not_startup_slack(tmp_path):
    settings = SandboxSettings(provider='fake', lifetime=100, startup_timeout=80, max_timeout=3600)

    async def run():
        started = time.time()
        box, session = kernel(tmp_path, settings, timeout=30)
        try:
            assert (await box.execute('x')).ok
        finally:
            await box.close()
        expires = box.runtime.handle['expires_at']
        assert expires - started <= 100 + 5
        assert expires - started < 100 + 80 - 10
        assert session.starts == 1 and session.renewals == []
    asyncio.run(run())


def test_near_expiry_renews_within_the_server_cap(tmp_path):
    async def run():
        box, session = kernel(tmp_path)
        try:
            assert (await box.execute('first')).ok and session.renewals == []
            box.runtime.save(expires_at=time.time() + 10, born_at=time.time() - 4000)
            assert (await box.execute('second')).ok
            assert session.starts == 1
            assert session.renewals == [renewal_seconds(box.settings, box.timeout)]
            assert session.renewals[0] <= 3600
            assert session.renewals[0] >= box.timeout
        finally:
            await box.close()
    asyncio.run(run())


def test_refused_renewal_replaces_the_sandbox_before_the_next_call(tmp_path):
    async def run():
        box, session = kernel(tmp_path)
        box.fs.write_text('data/input.csv', 'from-workspace')
        try:
            assert (await box.execute('warmup')).ok
            session.renew_error = SandboxLifetimeCap('超过 3600 秒')
            session.wipe_on_failure = False
            if session.remote.exists():
                shutil.rmtree(session.remote)
            box.runtime.save(expires_at=time.time() + 10, born_at=time.time() - 4000)
            result = await box.execute('print(1)')
            assert result.ok and session.calls == ['warmup', 'print(1)']
            assert session.starts == 2
            assert '单次寿命上限' in result.recovery_note
            assert '已提前更换并继续' in result.recovery_note
            assert (session.remote / 'data/input.csv').read_text() == 'from-workspace'
            assert 'data/input.csv' in session.uploads
            assert '已提前更换并继续' in result.to_observation()
        finally:
            await box.close()
    asyncio.run(run())


def test_clamped_renewal_also_replaces_before_computing(tmp_path):
    async def run():
        box, session = kernel(tmp_path)
        try:
            await box.execute('warmup')
            session.renew_grant = 10
            box.runtime.save(expires_at=time.time() + 10, born_at=time.time() - 4000)
            result = await box.execute('continue')
            assert result.ok and session.starts == 2 and session.calls[-1] == 'continue'
            assert '已提前更换并继续' in result.recovery_note
        finally:
            await box.close()
    asyncio.run(run())


def test_confirmed_loss_rebuilds_once_and_restores_workspace_files(tmp_path):
    async def run():
        box, session = kernel(tmp_path)
        box.fs.write_text('data/input.csv', 'from-workspace')
        session.fail_execute = 1
        session.wipe_on_failure = True
        try:
            result = await box.execute('compute-year')
            assert result.ok and result.stdout.strip() == 'ok'
            assert session.calls == ['compute-year', 'compute-year']
            assert session.starts == 2
            assert result.recovery_note == RETRIED_NOTE
            assert RETRIED_NOTE in result.to_observation()
            assert (session.remote / 'data/input.csv').read_text() == 'from-workspace'
            failed = next(row for row in box.fs._manifest['runs'].values() if row['status'] == 'error')
            assert failed['failure_kind'] == 'sandbox_lost'
            assert box.fs.read_text(failed['stdout']) == ''
        finally:
            await box.close()
    asyncio.run(run())


def test_rebuild_failure_is_reported_once(tmp_path):
    async def run():
        box, session = kernel(tmp_path)
        session.fail_execute = 1
        session.fail_next_start = False

        async def execute(code, run_id, output):
            session.calls.append(code)
            session.fail_next_start = True
            raise SandboxApiException()
        session.execute = execute
        try:
            result = await box.execute('compute-year')
            assert session.calls == ['compute-year']
            assert session.starts == 2
            assert result.failure_kind == 'sandbox_lost'
            assert result.error.startswith('重建失败：')
            assert '镜像拉取失败' in result.error
            assert result.recovery_note == result.error
        finally:
            try:
                await box.close()
            except RuntimeError:
                pass
    asyncio.run(run())


def test_retry_still_failing_stops_after_one_attempt(tmp_path):
    async def run():
        box, session = kernel(tmp_path)
        session.fail_execute = 5
        session.wipe_on_failure = True
        try:
            result = await box.execute('compute-year')
            assert session.calls == ['compute-year', 'compute-year']
            assert session.starts == 2
            assert not result.ok
            assert result.error.startswith('计算沙箱已到期/已断开，已自动重建并重试，仍然失败：')
            assert 'peer closed connection' in result.error
            assert '重建失败' not in result.error
        finally:
            await box.close()
    asyncio.run(run())


def test_generic_disconnect_and_ambiguous_receipt_are_not_replayed(tmp_path):
    async def run():
        box, session = kernel(tmp_path)

        async def disconnect(code, run_id, output):
            session.calls.append(code)
            raise ConnectionError('connection lost')
        session.execute = disconnect
        lost = await box.execute('disconnect')
        assert session.calls == ['disconnect'] and lost.failure_kind == 'transport_unknown'
        assert '不会自动重跑' not in (lost.error or '')

        async def ambiguous(code, run_id, output):
            session.calls.append(code)
            raise RuntimeError('沙箱输出连接已结束，但没有执行完成回执；不会自动重跑代码')
        session.execute = ambiguous
        unknown = await box.execute('ambiguous')
        assert session.calls == ['disconnect', 'ambiguous']
        assert unknown.failure_kind == 'transport_unknown'
        assert '不会自动重跑' in unknown.error
        await box.close()
    asyncio.run(run())


def test_finished_computation_is_not_rerun_when_delivery_drops(tmp_path):
    async def run():
        box, session = kernel(tmp_path)
        session.fail_download = True
        try:
            result = await box.execute('value\n1\n')
            assert result.ok and result.delivery_error and session.calls == ['value\n1\n']
            assert result.recovery_note is None
        finally:
            await box.close()
    asyncio.run(run())


def test_renewal_never_asks_for_more_than_the_server_cap():
    settings = SandboxSettings(lifetime=1800, startup_timeout=120, max_timeout=3600)
    assert granted_lifetime(settings) == 1800
    assert granted_lifetime(SandboxSettings(lifetime=7200, max_timeout=3600)) == 3600
    assert renewal_seconds(settings, 180) <= 3600
    assert renewal_seconds(settings, 5000) == 3600


def test_provider_sustain_renews_a_live_call_and_rejects_an_oversize_grant(tmp_path):
    from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
    session = OpenSandboxSession(
        SandboxSettings(api_key='test', host_workspace_root='/server/cache', lifetime=1800, max_timeout=3600),
        workspace_dir=tmp_path)
    calls = []
    done = asyncio.Event()

    async def renew(delta):
        calls.append(delta.total_seconds())
        done.set()
        return SimpleNamespace(expires_at=datetime.now(timezone.utc) + delta)

    session.sandbox = SimpleNamespace(renew=renew)
    session.expires_at = time.time() + 1
    session.id = 'box'

    async def run():
        with pytest.raises(SandboxLifetimeCap, match='3600'):
            await session.renew(3601)
        assert calls == []
        task = asyncio.create_task(session._sustain(session.settings.max_timeout))
        await asyncio.wait_for(done.wait(), 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert calls == [3600]
    asyncio.run(run())


def test_sandbox_errors_reach_the_user_instead_of_a_generic_controller_message():
    from ai_system.research.planner import planning_failure_message
    from ai_system.research.review_cache import error_detail
    snapshot = {'activity': [], 'experiments': {}, 'executions': {}}
    lost = SandboxApiException()
    message = planning_failure_message(lost, snapshot)
    assert '研究控制器未完成下一行动' not in message
    assert '计算沙箱已断开' in message
    assert 'peer closed connection' in message
    assert error_detail(lost).startswith('计算沙箱已断开')
    failed = SandboxUnavailable('重建失败：镜像拉取失败')
    assert '重建失败：镜像拉取失败' in planning_failure_message(failed, snapshot)
    assert error_detail(failed) == '重建失败：镜像拉取失败'


def test_research_tool_shows_the_retry_note_to_the_model_and_the_activity(tmp_path):
    from ai_system.graph.context import GraphContext
    from ai_system.modules.registry import invoke
    from ai_system.research.store import ResearchStore
    settings = SandboxSettings(provider='fake', lifetime=1800, max_timeout=3600)
    session = Expiring(tmp_path, settings)
    session.fail_execute = 1
    session.wipe_on_failure = True
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    ctx.research = ResearchStore(tmp_path, 'lifetime')
    ctx.research.initialize('计算并保留结果')
    ctx.kernels._kernels['research'] = RemoteKernel(tmp_path, session, settings, fs=ctx.fs)
    ctx.fs.write_text('data/input.csv', 'from-workspace')

    async def run():
        try:
            await invoke(ctx, 'research.update_question', question='计算全年结果')
            qid = next(iter(ctx.research.snapshot()['questions']))
            result = await invoke(ctx, 'research.run_python', question_id=qid, code='compute-year', title='跑全年模型')
            assert result['execution_status'] == 'succeeded'
            assert RETRIED_NOTE in result['observation']
            row = next(a for a in ctx.research.snapshot()['activity'] if a['capability'] == 'research.run_python')
            assert row['status'] == 'completed'
            assert row['message'] == RETRIED_NOTE
            assert session.calls == ['compute-year', 'compute-year']
        finally:
            await ctx.kernels.close()
    asyncio.run(run())
