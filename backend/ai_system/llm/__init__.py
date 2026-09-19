from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {name}")
    return path.read_text(encoding="utf-8")


__all__ = ["PROMPTS_DIR", "load_prompt"]
