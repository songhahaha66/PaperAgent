"""Provider-neutral execution contract. Providers never own research state."""
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .kernel import CellResult

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
    """One isolated, stateful Python session; paths are workspace-relative.

    Implementations must not retry submitted code after an ambiguous failure.
    close terminates remote computation, not just the client connection.
    """
    id: str | None

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
    async def finalize(self, run_id: str) -> None: ...  # optional, repeatable output preparation
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...
    async def release(self) -> None: ...  # healthy lease; provider may retain it until idle expiry


class ExecutionKernel(Protocol):
    timeout: float
    async def execute(self, code: str, *, on_event: EventHandler | None = None) -> "CellResult": ...
    async def close(self) -> None: ...
