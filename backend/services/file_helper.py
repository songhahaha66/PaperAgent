from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from fastapi import HTTPException, status


class FileHelper:
    """通用文件操作助手，封装常见路径与读写逻辑。"""

    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str | Path) -> Path:
        target = self.base_path / Path(relative_path)
        # 防目录穿越
        try:
            target.absolute().relative_to(self.base_path.absolute())
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")
        return target

    def read_text(self, relative_path: str, *, encoding: str = "utf-8", max_bytes: Optional[int] = 10 * 1024 * 1024) -> str:
        target = self.resolve(relative_path)
        if not target.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        if not target.is_file():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is not a file")
        if max_bytes is not None and target.stat().st_size > max_bytes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large to read")
        return target.read_text(encoding=encoding)

    def write_text(self, relative_path: str, content: str, *, encoding: str = "utf-8") -> str:
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)
        return str(target)

    def write_bytes_stream(self, relative_path: str, stream, *, max_bytes: Optional[int] = 50 * 1024 * 1024) -> str:
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as f:
            written = 0
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if max_bytes is not None and written > max_bytes:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large (max 50MB)")
                f.write(chunk)
        return str(target)

    def delete(self, relative_path: str) -> None:
        target = self.resolve(relative_path)
        if not target.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File or directory not found")
        if target.is_file():
            target.unlink()
        else:
            import shutil
            shutil.rmtree(target)


