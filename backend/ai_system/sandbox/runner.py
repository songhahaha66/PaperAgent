from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunResult:
    stdout: str
    stderr: str
    returncode: int
    artifacts: list[str] = field(default_factory=list)


def run_sandbox(code: str, workdir: Path | str, timeout: int = 30) -> RunResult:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / "_sandbox_job.py"
    script.write_text(code, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    artifacts = [
        str(path.relative_to(workdir))
        for path in workdir.iterdir()
        if path.is_file() and path.name != "_sandbox_job.py"
    ]
    return RunResult(
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
        artifacts=artifacts,
    )
