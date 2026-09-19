from __future__ import annotations

import os


def pipeline_version() -> str:
    value = (os.getenv("AI_PIPELINE") or "legacy").strip().lower()
    return value if value in {"legacy", "v2"} else "legacy"
