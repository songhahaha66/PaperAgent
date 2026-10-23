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


@dataclass(frozen=True)
class SandboxFile:
    path: str
    size: int
    sha256: str


class SandboxSession(Protocol):
    """One isolated execution session for Python, compilation and file transfer.

    Implementations must not retry submitted code after an ambiguous failure.
    close terminates remote computation, not just the client connection.
    root is the absolute POSIX workspace path inside the sandbox. File API
    paths are relative to it; backend host paths never cross this boundary.
    Frozen files must remain retrievable by cache_key after close/recreation.
    Python memory is retained only during the live session.
    """
    id: str | None
    root: str
    context_id: str | None

    async def start(self) -> None: ...
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
    async def execute(self, code: str, run_id: str, output: OutputHandler) -> ExecutionOutcome: ...
    async def exec(self, argv: Sequence[str], *, stdin: str | None = None, timeout: float = 180) -> tuple[int, str, str]: ...
    async def finalize(self, run_id: str) -> None: ...  # repeatable preparation; explicit no-op if unnecessary
    async def interrupt(self) -> None: ...
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
