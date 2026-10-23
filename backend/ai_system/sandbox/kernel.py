"""
持久 Python 内核：每个求解任务一个独立子进程，变量在多次执行间保留

与旧 CodeExecutor 的区别：
- 旧实现每次调用都起新进程，数据要反复加载，上一步算出的变量下一步拿不到；
- 这里一个内核进程服务整个子问题的求解，像 Jupyter 一样逐格执行；
- 每次执行仍记录为一条 run（runs/<run_id>/input.py、stdout.log、artifacts/），
  前端文件视图和评测里的"代码成功率"不受影响。
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from services.file_services.workspace_fs import WorkspaceFS

logger = logging.getLogger(__name__)

WORKER = Path(__file__).with_name("kernel_worker.py")
DEFAULT_TIMEOUT = 180.0
STREAM_LIMIT = 32 * 1024 * 1024


@dataclass
class CellResult:
    ok: bool
    stdout: str
    error: Optional[str]
    run_id: str
    # 相对工作空间的图片路径
    figures: List[str] = field(default_factory=list)
    timed_out: bool = False
    files: List[str] = field(default_factory=list)
    delivery_error: Optional[str] = None
    failure_kind: Optional[str] = None

    def to_observation(self) -> str:
        """给模型看的执行结果"""
        parts = [f"[run_id: {self.run_id}]"]
        if self.stdout.strip():
            parts.append(self.stdout.rstrip())
        if self.figures:
            parts.append("已保存图片: " + ", ".join(self.figures))
        if self.files:
            parts.append("已取回文件: " + ", ".join(self.files))
        if self.delivery_error:
            parts.append("计算已结束，文件交付未完成：" + self.delivery_error + "；请调用 retry_outputs，不要重算。")
        if self.error:
            parts.append(self.error.rstrip())
        if self.timed_out:
            parts.append("执行超时，内核已重启，之前定义的变量都已丢失，需要重新加载数据。")
        if len(parts) == 1:
            parts.append("（执行成功，无输出）")
        return "\n".join(parts)


class PythonKernel:
    def __init__(
        self,
        workspace_dir: str,
        timeout: float = DEFAULT_TIMEOUT,
        fs: Optional[WorkspaceFS] = None,
    ):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.timeout = timeout
        # 同一工作空间的多个内核必须共用一个 WorkspaceFS，否则各自的 manifest 副本会互相覆盖
        self.fs = fs or WorkspaceFS(str(self.workspace_dir))
        self.fs.init_structure()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()

    async def _ensure_started(self) -> asyncio.subprocess.Process:
        if self._proc is not None and self._proc.returncode is None:
            return self._proc
        env = os.environ.copy()
        env.update({
            "PYTHONIOENCODING": "utf-8",
            "MPLBACKEND": "Agg",
            # 限制数值库线程数，避免多个内核并行时互相抢 CPU
            "OMP_NUM_THREADS": env.get("OMP_NUM_THREADS", "2"),
            "OPENBLAS_NUM_THREADS": env.get("OPENBLAS_NUM_THREADS", "2"),
            "PYTHONPATH": str(self.workspace_dir / "code"),
        })
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", str(WORKER),
            cwd=str(self.workspace_dir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            limit=STREAM_LIMIT,
        )
        return self._proc

    async def execute(self, code: str, *, on_event=None) -> CellResult:
        async with self._lock:
            run_id = self.fs.create_run()
            self.fs.save_run_input(run_id, code)
            artifacts_dir = str(self.fs.run_dir(run_id) / "artifacts")
            from .artifacts import output_hashes, capture_local_outputs
            before = output_hashes(self.fs)
            self.fs.save_run_stdout(run_id, '')
            self.fs.save_run_stderr(run_id, '')
            proc = await self._ensure_started()
            sequence = 0
            async def event(kind, **payload):
                nonlocal sequence
                sequence += 1
                if on_event:
                    await on_event(dict(type=kind, run_id=run_id, seq=sequence, **payload))
            await event('running')

            request = {"id": uuid.uuid4().hex, "code": code, "artifacts_dir": artifacts_dir}
            proc.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
            await proc.stdin.drain()

            try:
                async def receive():
                    while True:
                        line = await proc.stdout.readline()
                        if not line: raise RuntimeError('内核进程意外退出')
                        message = json.loads(line)
                        if message.get('type') != 'output': return message
                        with (self.fs.run_dir(run_id) / (message['channel'] + '.log')).open('a') as stream:
                            stream.write(message['text'])
                        await event('output', channel=message['channel'], text=message['text'])
                response = await asyncio.wait_for(receive(), timeout=self.timeout)
                result = CellResult(
                    ok=response["ok"],
                    stdout=response.get("stdout") or "",
                    error=response.get("error"),
                    run_id=run_id,
                    figures=[self.fs.rel(path) for path in response.get("figures") or []],
                )
            except asyncio.CancelledError:
                await self._kill()
                self.fs.finish_run(run_id, 'cancelled')
                await event('finished', status='cancelled')
                raise
            except asyncio.TimeoutError:
                await self._kill()
                result = CellResult(
                    ok=False, stdout="", error=f"执行超过 {self.timeout:.0f} 秒", run_id=run_id, timed_out=True
                )
            except Exception as exc:
                await self._kill()
                result = CellResult(ok=False, stdout="", error=f"内核异常: {exc}", run_id=run_id)

            log = self.fs.read_text(f'runs/{run_id}/stdout.log')
            stderr = self.fs.read_text(f'runs/{run_id}/stderr.log')
            if not result.stdout: result.stdout = (log + stderr)[-20000:]
            if result.error:
                self.fs.save_run_stdout(run_id, log + '\n' + result.error)
            result.files = capture_local_outputs(self.fs, run_id, before)
            for figure in result.figures:
                self.fs.register_run_artifact(run_id, Path(figure).name)
            status = 'success' if result.ok else 'timeout' if result.timed_out else 'error'
            self.fs.finish_run(run_id, status)
            await event('finished', status=status, files=result.files, error=result.error)
            return result

    async def _kill(self):
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()
        self._proc = None

    async def close(self):
        if self._proc is not None and self._proc.returncode is None:
            self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.CancelledError:
                await self._kill()
                raise
            except asyncio.TimeoutError:
                await self._kill()
        self._proc = None

    async def __aenter__(self) -> "PythonKernel":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
