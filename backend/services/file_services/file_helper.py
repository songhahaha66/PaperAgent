from __future__ import annotations
import os
import tempfile
from pathlib import Path
from typing import Optional
from fastapi import HTTPException
from .workspace_layout import safe_join


def _directory(path, *, create=False):
    """Walk with dirfd/O_NOFOLLOW so link replacement cannot race validation."""
    path = Path(path).absolute()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in {'', '.', '..'}:
                raise ValueError('Invalid directory')
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = following
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_read(target):
    import stat
    target = Path(target)
    parent = _directory(target.parent)
    try:
        fd = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ValueError('Not a regular file')
        return os.fdopen(fd, 'rb')
    finally:
        os.close(parent)


def atomic_write(target, writer):
    """Commit through a pinned parent directory; failed writes keep the old file."""
    import stat
    import uuid
    target = Path(target)
    parent = _directory(target.parent, create=True)
    name = '.write-' + uuid.uuid4().hex
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if stat.S_ISLNK(os.stat(target.name, dir_fd=parent, follow_symlinks=False).st_mode):
                raise ValueError('Symbolic links are not allowed')
        except FileNotFoundError:
            pass
        os.replace(name, target.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(name, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


class FileHelper:
    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path).absolute()
        self.base_path.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str | Path) -> Path:
        try:
            return safe_join(self.base_path, relative_path)
        except ValueError as exc:
            raise HTTPException(400, 'Invalid path') from exc

    def read_text(self, relative_path: str, *, encoding='utf-8', max_bytes: Optional[int] = 10 * 1024 * 1024):
        target = self.resolve(relative_path)
        if not target.is_file():
            raise HTTPException(404, 'File not found')
        if max_bytes is not None and target.stat().st_size > max_bytes:
            raise HTTPException(400, 'File too large to read')
        with open_read(target) as stream:
            data = stream.read(max_bytes + 1 if max_bytes is not None else -1)
        if max_bytes is not None and len(data) > max_bytes:
            raise HTTPException(400, "File too large to read")
        return data.decode(encoding)

    def write_text(self, relative_path: str, content: str, *, encoding='utf-8'):
        target = self.resolve(relative_path)
        atomic_write(target, lambda f: f.write(content.encode(encoding)))
        return str(target)

    def write_bytes_stream(self, relative_path, stream, *, max_bytes=50 * 1024 * 1024):
        target = self.resolve(relative_path)
        def write(dest):
            written = 0
            while chunk := stream.read(1024 * 1024):
                written += len(chunk)
                if max_bytes is not None and written > max_bytes:
                    raise HTTPException(413, 'File too large')
                dest.write(chunk)
        atomic_write(target, write)
        return str(target)

    def delete(self, relative_path):
        target = self.resolve(relative_path)
        if not target.exists():
            raise HTTPException(404, 'File or directory not found')
        if target.is_file():
            target.unlink()
        else:
            import shutil
            shutil.rmtree(target)
