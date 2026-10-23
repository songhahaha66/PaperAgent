"""Per-file bounds apply while reading, not just to a racy pre-read stat."""
import hashlib
import os
from services.file_services.file_helper import open_read
from .settings import SandboxSettings

DEFAULT_MAX_FILE_BYTES = SandboxSettings.max_file_bytes


class FileLimitExceeded(ValueError):
    pass


def chunks(source, path, max_bytes):
    if os.fstat(source.fileno()).st_size > max_bytes:
        raise FileLimitExceeded(f'文件超过单文件上限 {max_bytes} bytes: {path.name}')
    total = 0
    while chunk := source.read(min(1024 * 1024, max_bytes - total + 1)):
        total += len(chunk)
        if total > max_bytes:
            raise FileLimitExceeded(f'文件读取期间超过单文件上限 {max_bytes} bytes: {path.name}')
        yield chunk


def bounded_hash(path, max_bytes):
    digest = hashlib.sha256()
    with open_read(path) as source:
        for chunk in chunks(source, path, max_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def bounded_copy(path, target, max_bytes):
    with open_read(path) as source:
        for chunk in chunks(source, path, max_bytes):
            target.write(chunk)
