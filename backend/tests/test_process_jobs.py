"""Real trusted subprocesses; no network, live sandbox or model access."""
import asyncio
import json

import pytest

from ai_system.sandbox.remote_kernel import RemoteKernel
from ai_system.sandbox.settings import SandboxSettings
from helpers.process_jobs import ProcessJobs


@pytest.mark.parametrize('operation', ['status', 'stop'])
def test_job_rpc_keeps_bootstrap_banner_out_of_json(tmp_path, operation):
    async def check():
        session = ProcessJobs(tmp_path / 'backend', tmp_path / 'server with spaces')
        value = await session._job_rpc(operation, 'run-banner')
        assert value['job_id'] == 'run-banner'
        code, out, err = session.rpc_outputs[-1]
        assert code == 0 and json.loads(out) == value
        assert 'Switched to Python Python 3.11.15' in err
        assert 'Switched to Python' not in out
    asyncio.run(check())


@pytest.mark.parametrize('operation', ['submit', 'status', 'stop'])
def test_job_rpc_bootstrap_failure_preserves_diagnostics_and_skips_worker(tmp_path, operation):
    async def check():
        session = ProcessJobs(tmp_path / 'backend', tmp_path / 'server')
        session.bootstrap.write_text('echo "environment setup failed"\necho "detail" >&2\nreturn 23\n')
        with pytest.raises(RuntimeError, match='environment setup failed\ndetail'):
            await session._job_rpc(operation, 'run-failed')
        assert session.rpc_outputs[-1] == (23, '', 'environment setup failed\ndetail\n')
        assert not (session.remote / '.pa-jobs').exists()
    asyncio.run(check())


@pytest.mark.parametrize('receipt, error', [
    ('not JSON', json.JSONDecodeError),
    ('{"job_id": "another-job"}', ValueError),
    ('{"job_id": "run-invalid"}\ntrailing output', json.JSONDecodeError),
])
def test_job_rpc_rejects_invalid_worker_receipts(tmp_path, receipt, error):
    async def check():
        session = ProcessJobs(tmp_path / 'backend', tmp_path / 'server')
        session.helper.write_text(f'print({receipt!r})\n')
        with pytest.raises(error):
            await session._job_rpc('status', 'run-invalid')
    asyncio.run(check())


def test_lost_submit_and_poll_replies_do_not_replay_code_and_processes_are_fresh(tmp_path):
    async def check():
        session = ProcessJobs(tmp_path / 'backend', tmp_path / 'server')
        session.lost_submit_reply = True
        session.read_failures = 2
        kernel = RemoteKernel(session.backend_root, session, session.settings, timeout=15)
        events = []
        async def event(value):
            events.append(value)
        try:
            result = await kernel.execute(
                "from pathlib import Path\nimport sys\nx = 42\n"
                "p = Path('outputs/count.txt')\np.write_text(p.read_text() + '1' if p.exists() else '1')\n"
                "print('开始', flush=True)\nprint('警告', file=sys.stderr, flush=True)\nx", on_event=event)
            assert result.ok and result.exit_code == 0 and result.duration_seconds > 0
            assert '开始' in result.stdout and '警告' in result.stdout and '42' in result.stdout
            assert (session.backend_root / 'outputs/count.txt').read_text() == '1'
            assert sum(op == 'submit' for op, _ in session.operations) == 2
            assert all('Switched to Python' not in out and 'Switched to Python' in err
                       for _, out, err in session.rpc_outputs)
            receipt = json.loads((kernel.fs.run_dir(result.run_id) / 'done.json').read_text())
            assert receipt['exit_code'] == 0 and receipt['job_id'] == result.run_id
            assert any(e['type'] == 'output' for e in events)
            next_result = await kernel.execute("print('x' in globals())")
            assert next_result.ok and next_result.stdout.strip() == 'False'
            assert session.closes == 0
        finally:
            await kernel.close()
    asyncio.run(check())


def test_timeout_escalates_process_signals_preserves_logs_and_common_patch(tmp_path):
    async def check():
        session = ProcessJobs(tmp_path / 'backend', tmp_path / 'server')
        kernel = RemoteKernel(session.backend_root, session, session.settings, timeout=3)
        kernel.fs.write_text('code/common.py', 'original = True\n')
        try:
            result = await kernel.execute(
                "from pathlib import Path\nimport signal, time, sys\n"
                "signal.signal(signal.SIGINT, signal.SIG_IGN)\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "Path('code/common.py').write_text('patched = True\\n')\n"
                "print('partial stdout', flush=True)\nprint('partial stderr', file=sys.stderr, flush=True)\n"
                "time.sleep(60)")
            assert result.timed_out and not result.ok and result.exit_code == -9
            assert result.duration_seconds >= 5
            assert 'partial stdout' in result.stdout and 'partial stderr' in result.stdout
            assert not result.delivery_error and session.closes == 0 and kernel._started
            assert (session.backend_root / 'code/common.py').read_text() == 'patched = True\n'
            assert (kernel.fs.run_dir(result.run_id) / 'artifacts/code/common.py').read_text() == 'patched = True\n'
            assert kernel.fs.get_run_info(result.run_id)['compute_state'] == 'timed_out'
            kernel.timeout = 15
            following = await kernel.execute("import common\nprint(common.patched)")
            assert following.ok and following.stdout.strip() == 'True'
            assert session.closes == 0
        finally:
            await kernel.close()
    asyncio.run(check())


def test_backend_reconnect_recovers_original_job_without_submission_or_duplicate_logs(tmp_path):
    async def check():
        settings = SandboxSettings(provider='fake')
        first = ProcessJobs(tmp_path / 'backend', tmp_path / 'server', settings=settings)
        kernel = RemoteKernel(first.backend_root, first, settings, timeout=3)
        first.block_stop = True
        async def disconnect_after_output(event):
            if event['type'] == 'output':
                first.offline = True
        result = await kernel.execute(
            "from pathlib import Path\nimport time\nprint('before disconnect', flush=True)\ntime.sleep(1.5)\n"
            "Path('code/common.py').write_text('recovered = True\\n')\nprint('after disconnect', flush=True)",
            on_event=disconnect_after_output)
        assert result.delivery_error and kernel.pending_run == result.run_id and first.closes == 0
        original_log = (kernel.fs.run_dir(result.run_id) / 'stdout.log').read_bytes()
        kernel.timeout = .1
        with pytest.raises(TimeoutError):
            await kernel.retry_outputs()
        assert (kernel.fs.run_dir(result.run_id) / 'stdout.log').read_bytes() == original_log
        kernel.timeout = 3
        with pytest.raises(RuntimeError, match='暂不销毁'):
            await kernel.close()
        second = ProcessJobs(first.backend_root, tmp_path / 'server', settings=settings)
        resumed = RemoteKernel(first.backend_root, second, settings, timeout=15)
        try:
            assert resumed.pending_run == result.run_id
            assert await resumed.retry_outputs() == result.run_id
            assert not any(op == 'submit' for op, _ in second.operations)
            log = (resumed.fs.run_dir(result.run_id) / 'stdout.log').read_text()
            assert log.count('before disconnect') == log.count('after disconnect') == 1
            assert (first.backend_root / 'code/common.py').read_text() == 'recovered = True\n'
            info = resumed.fs.get_run_info(result.run_id)
            # Its sandbox-side watchdog can finish it while the backend is offline.
            assert info['process_receipt']['job_id'] == result.run_id and not info['delivery_error']
            assert first.closes == 0
        finally:
            await resumed.close()
    asyncio.run(check())


def test_process_logs_are_bounded_and_keep_multibyte_boundaries(tmp_path):
    async def check():
        session = ProcessJobs(tmp_path / 'backend', tmp_path / 'server')
        kernel = RemoteKernel(session.backend_root, session, session.settings, timeout=15)
        try:
            result = await kernel.execute("print('a' * 65535 + '中文' * 100)")
            assert result.ok
            raw = (kernel.fs.run_dir(result.run_id) / 'stdout.log').read_text()
            assert raw == 'a' * 65535 + '中文' * 100 + '\n'
        finally:
            await kernel.close()
        limited = ProcessJobs(tmp_path / 'limited-backend', tmp_path / 'limited-server',
                              settings=SandboxSettings(provider='fake', max_log_bytes=128))
        kernel = RemoteKernel(limited.backend_root, limited, limited.settings, timeout=15)
        try:
            result = await kernel.execute("print('x' * 1000000)")
            assert not result.ok and result.failure_kind == 'resource' and not result.delivery_error
            assert sum((kernel.fs.run_dir(result.run_id) / (name + '.log')).stat().st_size
                       for name in ('stdout', 'stderr')) <= 128
            assert limited.closes == 0
        finally:
            await kernel.close()
    asyncio.run(check())


def test_failed_process_returns_traceback_and_flushes_figures(tmp_path):
    async def check():
        session = ProcessJobs(tmp_path / 'backend', tmp_path / 'server')
        kernel = RemoteKernel(session.backend_root, session, session.settings, timeout=15)
        try:
            result = await kernel.execute("import matplotlib.pyplot as plt\nplt.plot([1, 2])\nprint('before error')\n1/0")
            assert not result.ok and result.exit_code == 1 and result.failure_kind == 'code'
            assert 'before error' in result.stdout and 'ZeroDivisionError' in result.stdout
            assert result.figures and all(kernel.fs.abs(path).is_file() for path in result.figures)
            assert not result.delivery_error and session.closes == 0
        finally:
            await kernel.close()
    asyncio.run(check())


def test_cancelling_backend_task_stops_only_its_process_and_recovers_files(tmp_path):
    async def check():
        session = ProcessJobs(tmp_path / 'backend', tmp_path / 'server')
        kernel = RemoteKernel(session.backend_root, session, session.settings, timeout=15)
        ready = asyncio.Event()
        async def event(value):
            if value['type'] == 'output':
                ready.set()
        task = asyncio.create_task(kernel.execute(
            "from pathlib import Path\nimport time\nPath('code/common.py').write_text('saved = True\\n')\n"
            "print('ready to cancel', flush=True)\ntime.sleep(60)", on_event=event))
        try:
            await asyncio.wait_for(ready.wait(), 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 15)
            info = next(iter(kernel.fs._manifest['runs'].values()))
            assert info['status'] == 'cancelled' and info['process_receipt']['stop_reason'] == 'cancelled'
            assert not info['delivery_error'] and session.closes == 0
            assert (session.backend_root / 'code/common.py').read_text() == 'saved = True\n'
            assert 'ready to cancel' in kernel.fs.read_text(info['stdout'])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await kernel.close()
    asyncio.run(check())
