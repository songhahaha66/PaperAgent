"""Separate runtime serialization from short atomic document/file commits."""
import asyncio
import fcntl
import json
import time
from contextlib import contextmanager, asynccontextmanager
from fastapi import HTTPException
from config.paths import get_workspaces_path
from .workspace_layout import private_root
from pathlib import Path


def project_root(root):
    root = Path(root).absolute()
    base = get_workspaces_path().absolute()
    return base / root.relative_to(base).parts[0] if root.is_relative_to(base) else root


def _open(root, *, runtime=False):
    path = private_root(project_root(root)) / ('runtime.lock' if runtime else 'operation.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open('a+b')


def _check_lease(root, *, cache_key=None, allowed_leases=()):
    path = private_root(project_root(root)) / 'sandbox-lease.json'
    if path.exists():
        lease = json.loads(path.read_text())
        rows=lease.get('leases',{'previous':lease})
        pending=[row for key,row in rows.items() if key not in allowed_leases
                 and row['expires_at']>time.time()
                 and (cache_key is None or row.get('cache_key') in {None,cache_key})]
        if pending:
            raise HTTPException(409, '上个沙箱尚未确认销毁，请等待其 TTL 到期后重试；不会重跑中断代码')
        if lease['expires_at']<=time.time():path.unlink()


@contextmanager
def edit_lock(root):
    with _open(root) as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(409, '项目文件正在提交，请稍后重试修改') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@asynccontextmanager
async def execution_lock(root, *, cache_key=None, allowed_leases=()):
    with _open(root,runtime=True) as stream:
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
        try:
            _check_lease(root,cache_key=cache_key,allowed_leases=allowed_leases)
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
