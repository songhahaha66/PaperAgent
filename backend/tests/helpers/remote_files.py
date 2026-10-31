"""A separate server filesystem. Only upload/download may cross the boundary.

No generated Python or compiler command is executed by this fake. Trusted static
file-transport routines run as ordinary pure unit functions on the remote tree.
"""
import hashlib
import json
import uuid
from contextlib import contextmanager
from pathlib import Path
from ai_system.sandbox import transfer_worker
from ai_system.sandbox.settings import SandboxSettings


class RemoteFiles:
    root='/workspace'
    def __init__(self, backend_root, server_root=None, *, cache_key=None, settings=None):
        self.backend_root=Path(backend_root)
        self.cache_key=cache_key or hashlib.sha256(str(self.backend_root).encode()).hexdigest()
        self.remote=Path(server_root or self.backend_root.parent.parent/'separate-server')/self.cache_key
        assert not self.remote.is_relative_to(self.backend_root) and self.remote!=self.backend_root
        self.volume_path='/server-only/cache/'+self.cache_key
        self.settings=settings or SandboxSettings(provider='fake')
        self.uploads=[];self.downloads=[];self.deleted=[];self.calls=[]
        self.closed=False;self.started=False;self.fail_download=False;self.corrupt_download=False
        self.id=None;self.context_id=None

    @contextmanager
    def worker(self):
        previous=transfer_worker.ROOT;transfer_worker.ROOT=self.remote
        try:yield
        finally:transfer_worker.ROOT=previous

    async def start(self):
        self.remote.mkdir(parents=True,exist_ok=True)
        for name in ('data','attachment','code','outputs/intermediate','.build/latex'):(self.remote/name).mkdir(parents=True,exist_ok=True)
        self.id='fake-'+uuid.uuid4().hex;self.context_id=uuid.uuid4().hex
        self.started=True;self.closed=False

    async def inventory(self, paths=None, *, kind='python'):
        with self.worker():return transfer_worker.inventory(kind,paths,self.settings.max_file_bytes)

    async def upload(self,path,source,sha256):
        with self.worker():target=transfer_worker.path(path)
        raw=Path(source).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==sha256
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        self.uploads.append(path)

    async def input_versions(self):
        ledger=self.remote/'.pa-inputs.json'
        return json.loads(ledger.read_text()) if ledger.exists() else {}

    async def save_input_versions(self, versions):
        (self.remote/'.pa-inputs.json').write_text(json.dumps(versions))

    async def delete_input(self, path):
        with self.worker():transfer_worker.path(path).unlink(missing_ok=True)
        self.deleted.append(path)

    async def capture(self,run_id,baseline,*,kind='python'):
        with self.worker():return transfer_worker.freeze(run_id,baseline,kind,self.settings.max_file_bytes)

    async def capture_inputs(self,run_id,baseline,paths):
        with self.worker():return transfer_worker.freeze(run_id,baseline,'inputs',self.settings.max_file_bytes,paths)

    async def frozen(self,run_id):
        return json.loads((self.remote/'.pa-runs'/run_id/'manifest.json').read_text())

    async def download(self,snapshot,destination,max_bytes):
        self.downloads.append(snapshot)
        if self.fail_download:raise ConnectionError('mock delivery disconnected')
        with self.worker():source=transfer_worker.path(snapshot,True)
        raw=source.read_bytes();assert len(raw)<=max_bytes
        Path(destination).write_bytes(b'wrong bytes' if self.corrupt_download else raw)

    async def finalize(self,run_id):pass
    async def attach(self,handle):
        self.id=handle['sandbox_id'];self.context_id=handle['context_id']
        self.started=True;self.closed=False;self.context_restored=True
    async def attach_for_destroy(self,handle):await self.attach(handle)
    async def disconnect(self):self.started=False
    async def interrupt(self):self.interrupted=True
    async def close(self):self.closed=True;self.started=False
