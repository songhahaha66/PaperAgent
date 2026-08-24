import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.chat_services.task_manager import TaskManager, TaskStatus


def _fresh_manager(timeout: float) -> TaskManager:
    TaskManager._instance = None
    mgr = TaskManager()
    mgr._task_timeout = timeout
    return mgr


def test_default_timeout_is_two_hours():
    TaskManager._instance = None
    mgr = TaskManager()
    assert mgr._task_timeout == 7200


def test_watchdog_cancels_running_task_without_status_poll():
    async def _run():
        mgr = _fresh_manager(0.05)
        work_id = "timeout-work"
        mgr.create_task(work_id, 1, "完成作业")
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def work():
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                mgr.cancel_task(work_id)
                raise

        mgr.start_task(work_id)
        ai_task = asyncio.create_task(work())
        mgr.set_async_task(work_id, ai_task)
        await started.wait()
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        await asyncio.sleep(0)
        task = mgr.get_task(work_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED
        assert task.error == "任务超时"
        assert ai_task.done()
        status = mgr.get_task_status(work_id)
        assert status["status"] == "failed"
        assert status["error"] == "任务超时"

    asyncio.run(_run())


def test_complete_task_disarms_watchdog():
    async def _run():
        mgr = _fresh_manager(0.05)
        work_id = "ok-work"
        mgr.create_task(work_id, 1, "完成作业")

        async def work():
            await asyncio.sleep(0.2)

        mgr.start_task(work_id)
        ai_task = asyncio.create_task(work())
        mgr.set_async_task(work_id, ai_task)
        mgr.complete_task(work_id)
        assert mgr.get_task(work_id).status == TaskStatus.COMPLETED
        await asyncio.sleep(0.15)
        assert not ai_task.cancelled()
        await ai_task

    asyncio.run(_run())
