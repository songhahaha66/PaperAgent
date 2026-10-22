import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.sandbox import PythonKernel


def _run(coro):
    return asyncio.run(coro)


def _manifest(ws: Path) -> dict:
    return json.loads((ws / "manifest.json").read_text(encoding="utf-8"))


def test_variables_persist_between_cells(tmp_path):
    async def go():
        async with PythonKernel(str(tmp_path)) as kernel:
            first = await kernel.execute("x = 21\nprint('set')")
            second = await kernel.execute("print(x * 2)")
            return first, second

    first, second = _run(go())
    assert first.ok and first.stdout.strip() == "set"
    assert second.ok and second.stdout.strip() == "42"
    runs = _manifest(tmp_path)["runs"]
    assert runs[first.run_id]["status"] == "success"
    assert (tmp_path / "runs" / second.run_id / "input.py").read_text(encoding="utf-8") == "print(x * 2)"
    assert (tmp_path / "runs" / second.run_id / "stdout.log").read_text(encoding="utf-8").strip() == "42"


def test_error_keeps_user_traceback_and_kernel_alive(tmp_path):
    async def go():
        async with PythonKernel(str(tmp_path)) as kernel:
            await kernel.execute("y = 1")
            failed = await kernel.execute("print('before')\n1 / 0")
            after = await kernel.execute("print(y)")
            return failed, after

    failed, after = _run(go())
    assert not failed.ok
    assert "before" in failed.stdout
    assert "ZeroDivisionError" in failed.error
    assert "kernel_worker" not in failed.error
    assert after.ok and after.stdout.strip() == "1"
    assert _manifest(tmp_path)["runs"][failed.run_id]["status"] == "error"


def test_timeout_restarts_kernel(tmp_path):
    async def go():
        async with PythonKernel(str(tmp_path), timeout=30) as kernel:
            initial = await kernel.execute("z = 5")
            assert initial.ok
            # Only the deliberate stall has a short deadline; cold starts need
            # time to import the worker's scientific libraries on busy machines.
            kernel.timeout = 1
            slow = await kernel.execute("import time\ntime.sleep(10)")
            kernel.timeout = 30
            fresh = await kernel.execute("print('z' in globals())")
            return slow, fresh

    slow, fresh = _run(go())
    assert slow.timed_out and not slow.ok
    assert "变量都已丢失" in slow.to_observation()
    assert fresh.ok and fresh.stdout.strip() == "False"


def test_kernels_sharing_fs_do_not_lose_runs(tmp_path):
    async def go():
        first = PythonKernel(str(tmp_path))
        second = PythonKernel(str(tmp_path), fs=first.fs)
        try:
            results = await asyncio.gather(
                first.execute("print(1)"), second.execute("print(2)"), first.execute("print(3)")
            )
        finally:
            await first.close()
            await second.close()
        return results

    results = _run(go())
    runs = _manifest(tmp_path)["runs"]
    assert {r.run_id for r in results} <= set(runs)
    assert all(runs[r.run_id]["status"] == "success" for r in results)
