"""Project-private immutable bytes and snapshots, independent of working paths.

Only private run archives may hard-link blobs. Mutable public files are copied.
Remote bytes become available only after their size and hash have been checked.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from services.file_services.workspace_layout import evidence_root, safe_join
from services.file_services.file_helper import atomic_write
from .file_limits import bounded_hash, bounded_copy


class ContentStore:
    def __init__(self, workspace, max_bytes):
        self.root = evidence_root(Path(workspace)) / 'content'
        self.max_bytes = max_bytes
        self._verified = {}

    def path(self, version):
        if not re.fullmatch(r'[a-f0-9]{64}', version):
            raise ValueError('Invalid content version')
        return safe_join(self.root, 'blobs/' + version[:2] + '/' + version[2:])

    def has(self, version, size=None):
        path = self.path(version)
        if not path.exists():
            return False
        stat=path.stat()
        identity=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        if (size is not None and stat.st_size != size) or (self._verified.get(version)!=identity
                and bounded_hash(path, self.max_bytes) != version):
            raise ValueError('Archived content failed integrity verification')
        self._verified[version]=identity
        return True

    def put(self, source, version=None):
        version = version or bounded_hash(source, self.max_bytes)
        target = self.path(version)
        if not self.has(version):
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='.content-', dir=target.parent)
            os.close(fd)
            temporary = Path(name)
            try:
                with temporary.open('wb') as stream:
                    bounded_copy(source, stream, self.max_bytes)
                if bounded_hash(temporary, self.max_bytes) != version:
                    raise ValueError('File changed while archiving content')
                # Link installs without replacing a concurrently archived version.
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    self.has(version)
            finally:
                temporary.unlink(missing_ok=True)
        return version

    def link_archive(self, version, target):
        if not self.has(version):
            raise ValueError('Content is not available in the backend')
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self.path(version)
        if target.exists() and (os.path.samefile(source,target) or bounded_hash(target, self.max_bytes) == version):
            return
        fd, name = tempfile.mkstemp(prefix='.archive-', dir=target.parent)
        os.close(fd)
        temporary = Path(name)
        temporary.unlink()
        try:
            os.link(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def snapshot(self, entries):
        for name, row in entries.items():
            safe_join(self.root, 'paths/' + name)
            if not self.has(row['sha256'], row['size']):
                raise ValueError('Snapshot contains unavailable content: ' + name)
        raw = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        version = hashlib.sha256(raw).hexdigest()
        target = safe_join(self.root, 'snapshots/' + version + '.json')
        if not target.exists():
            atomic_write(target, lambda stream: stream.write(raw))
        return version

    def materialize(self, version, target):
        """Public working copies never share an inode with immutable blobs."""
        if not self.has(version):
            raise ValueError('Content is not available in the backend')
        target = Path(target)
        if target.is_relative_to(self.root.parent):
            self.link_archive(version, target)
        else:
            atomic_write(target, lambda stream: bounded_copy(self.path(version), stream, self.max_bytes))
