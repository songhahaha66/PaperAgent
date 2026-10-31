"""Durable runtime ownership. Business calls disconnect; only reset destroys.

The provider reports creation immediately, before interpreter initialization or
file transfer. An ambiguous attach never silently creates a replacement.
"""
import hashlib
import json
import time
import fcntl
from contextlib import contextmanager
from pathlib import Path
from services.file_services.workspace_layout import private_root, safe_join
from services.file_services.file_helper import atomic_write


class RuntimeGone(RuntimeError):
    """The provider confirmed that this runtime/context no longer exists."""


class LeaseLedger:
    @staticmethod
    @contextmanager
    def locked(path):
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.with_suffix('.lock').open('a+b') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(stream,fcntl.LOCK_UN)

    @staticmethod
    def save(path, identity, lease):
        with LeaseLedger.locked(path):
            data=json.loads(path.read_text()) if path.exists() else {}
            leases=data.get('leases',{})
            if data and 'leases' not in data:leases['previous']=data
            leases[identity]=lease
            atomic_write(path,lambda f:f.write(json.dumps({'leases':leases,
                'expires_at':max(r['expires_at'] for r in leases.values())}).encode()))

    @staticmethod
    def remove(path, identity):
        with LeaseLedger.locked(path):
            if not path.exists():return
            data=json.loads(path.read_text());leases=data.get('leases',{})
            if data and 'leases' not in data:leases['previous']=data
            leases.pop(identity,None)
            if not leases:path.unlink()
            else:atomic_write(path,lambda f:f.write(json.dumps({'leases':leases,
                'expires_at':max(r['expires_at'] for r in leases.values())}).encode()))


class RuntimeManager:
    def __init__(self, workspace, session, settings):
        self.session,self.settings=session,settings
        self.deployment=hashlib.sha256(json.dumps([settings.provider,settings.endpoint,
            settings.image,settings.cpu,settings.memory],sort_keys=True).encode()).hexdigest()
        self.path=safe_join(private_root(Path(workspace)),'.system/runtimes/'+session.cache_key+'.json')
        self.handle=None
        session.on_created=self.created
        if self.path.exists():
            old=json.loads(self.path.read_text())
            if old.get('deployment')==self.deployment and old.get('expires_at',0)>time.time() and old.get('lease_id'):
                session.lease_id=old['lease_id']

    def save(self, **values):
        self.handle={**(self.handle or {}),**values,'updated_at':time.time()}
        atomic_write(self.path,lambda f:f.write(json.dumps(self.handle).encode()))

    def created(self, sandbox_id):
        self.save(sandbox_id=sandbox_id,state='initializing')

    async def start(self):
        self.handle=json.loads(self.path.read_text()) if self.path.exists() else None
        old=self.handle or {}
        if old.get('deployment') not in {None,self.deployment} and old.get('expires_at',0)>time.time():
            raise RuntimeError('原沙箱属于另一运行环境；请先处理原运行，不会悄悄新建环境')
        if (old.get('state') in {'ready','detached'} and old.get('expires_at',0)>time.time()
                and old.get('deployment')==self.deployment):
            try:
                await self.session.attach(old)
            except RuntimeGone:
                self.save(state='lost')
            else:
                self.save(state='ready')
                return
        elif old.get('state') in {'creating','initializing','unknown'} and old.get('expires_at',0)>time.time():
            raise RuntimeError('原沙箱创建或清理状态尚未确认，请核实原运行；不会重复创建')
        self.save(deployment=self.deployment,provider=self.settings.provider,cache_key=self.session.cache_key,
            epoch=old.get('epoch',0)+1,state='creating',sandbox_id=None,context_id=None,
            lease_id=getattr(self.session,'lease_id',None),expires_at=time.time()+self.settings.lifetime+self.settings.startup_timeout)
        try:
            await self.session.start()
        except BaseException:
            self.save(state='unknown')
            raise
        self.save(state='ready',sandbox_id=self.session.id,context_id=self.session.context_id)

    async def disconnect(self):
        try:await self.session.disconnect()
        except Exception as exc:
            if self.handle:self.save(client_cleanup_error=type(exc).__name__)
            raise
        if self.handle and self.handle.get('state')=='ready':
            self.save(state='detached',client_cleanup_error=None)

    async def destroy(self):
        if self.handle is None and self.path.exists():self.handle=json.loads(self.path.read_text())
        try:
            if self.handle and self.handle.get('state') not in {'destroyed','lost'} and (not self.session.id or self.handle.get('state')=='detached'):
                if self.handle.get('sandbox_id'):
                    await self.session.attach_for_destroy(self.handle)
                elif self.handle.get('expires_at',0)>time.time():
                    raise RuntimeError('创建结果未知且没有沙箱ID，不能确认销毁；保留租约等待TTL，不会重复创建')
            await self.session.close()
        except BaseException:
            if self.handle:self.save(state='unknown')
            raise
        else:
            if self.handle:self.save(state='destroyed',expires_at=time.time())
