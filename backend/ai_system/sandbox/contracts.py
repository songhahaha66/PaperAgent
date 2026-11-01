"""Provider-neutral execution contract. Providers never own research state."""
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .kernel import CellResult
    from .settings import SandboxSettings

OutputHandler = Callable[[str, str], Awaitable[None]]
EventHandler = Callable[[dict], Awaitable[None]]


@dataclass(frozen=True)
class ExecutionOutcome:
    error: str | None = None
    exit_code: int | None = None
    duration_seconds: float | None = None
    stop_reason: str | None = None
    receipt: dict | None = None


@dataclass(frozen=True)
class SandboxFile:
    path: str
    size: int
    sha256: str


class SandboxSession(Protocol):
    """One isolated execution session for Python, compilation and file transfer.

    Implementations must not replay submitted process jobs after a failure.
    Submission handshakes are idempotent by run ID; only status/log reads may
    retry. Missing completion receipts remain unknown until recovered.
    close terminates remote computation, not just the client connection.
    root is the absolute POSIX workspace path inside the sandbox. File API
    paths are relative to it; backend host paths never cross this boundary.
    Frozen files must remain retrievable by cache_key after close/recreation.
    Python jobs use fresh processes; code and results persist as files. A process
    provider's recover reads the original job receipt, never resubmits its code.
    """
    id: str | None
    root: str
    context_id: str | None

    async def start(self) -> None: ...
    async def attach(self, handle: dict) -> None: ...
    async def attach_for_destroy(self, handle: dict) -> None: ...
    async def disconnect(self) -> None: ...  # client resources only; retain runtime
    cache_key: str
    async def inventory(self, paths=None, *, kind='python') -> list[dict]: ...
    async def upload(self, path: str, source: Path, sha256: str) -> None: ...
    async def input_versions(self) -> dict: ...
    async def save_input_versions(self, versions: dict) -> None: ...
    async def delete_input(self, path: str) -> None: ...
    async def capture(self, run_id: str, baseline: dict, *, kind='python') -> dict: ...
    async def capture_inputs(self, run_id: str, baseline: dict, paths: list[str]) -> dict: ...
    async def frozen(self, run_id: str) -> dict: ...
    async def download(self, snapshot: str, destination: Path, max_bytes: int) -> None: ...
    async def submit(self, code: str, run_id: str, *, timeout: float = 180) -> None: ...
    async def poll(self, run_id: str, output: OutputHandler) -> ExecutionOutcome | None: ...
    async def execute(self, code: str, run_id: str, output: OutputHandler, *, timeout: float = 180) -> ExecutionOutcome: ...
    async def recover(self, run_id: str, output: OutputHandler) -> ExecutionOutcome: ...
    async def exec(self, argv: Sequence[str], *, stdin: str | None = None, timeout: float = 180) -> tuple[int, str, str]: ...
    async def finalize(self, run_id: str) -> None: ...  # repeatable preparation; explicit no-op if unnecessary
    async def interrupt(self, reason: str = 'cancelled') -> ExecutionOutcome | None: ...
    async def close(self) -> None: ...


class SessionFactory(Protocol):
    """Trusted backend registration, never chosen by model tool arguments."""
    def __call__(self, settings: "SandboxSettings", *, workspace_dir: str | Path,
                 work_id: str | None = None, experiment_id: str | None = None,
                 cache_key: str | None = None) -> SandboxSession: ...


class ExecutionKernel(Protocol):
    timeout: float
    async def execute(self, code: str, *, on_event: EventHandler | None = None) -> "CellResult": ...
    async def close(self) -> None: ...
    async def disconnect(self) -> None: ...
