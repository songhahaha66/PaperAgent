"""Compiler inputs and completed outputs cross the file API, never a shared Path."""
import asyncio
import hashlib
import json
import uuid
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from .factory import create_session
from .settings import SandboxSettings
from .transfer import WorkspaceTransfer
from services.file_services.workspace_fs import WorkspaceFS
from services.file_services.workspace_layout import workspace_path
from services.file_services.file_helper import atomic_write
from services.file_services.workspace_lock import execution_lock, project_root

_ACTIVE=ContextVar('sandbox_compiler',default=None)


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def _save(path, data):
    atomic_write(path,lambda f:f.write(json.dumps(data,ensure_ascii=False).encode()))


def sandbox_operation(function=None, *, input_selector=None):
    if function is None:
        return lambda selected:sandbox_operation(selected,input_selector=input_selector)
    @wraps(function)
    async def wrapped(workspace,*args,**kwargs):
        root=Path(workspace).absolute()
        if _ACTIVE.get() is not None:
            return await function(root,*args,**kwargs)
        async with execution_lock(root):
            settings=SandboxSettings.from_env()
            fs=WorkspaceFS(root)
            # Stable snapshot identity survives the local TemporaryDirectory name.
            from .transfer import input_files
            from .file_limits import bounded_hash
            verified_snapshot=kwargs.get('verified_snapshot',False)
            input_names=input_selector(root,*args,**kwargs) if input_selector else None
            inputs={p:bounded_hash(v,settings.max_file_bytes) for p,v in input_files(fs,compiler=True,verified_snapshot=verified_snapshot,names=input_names).items() if p!='.build/reference.docx'}
            semantic_args=json.dumps([args,kwargs],default=str,sort_keys=True).replace(str(root),'/workspace')
            cache_key=_digest(['compiler-api-v1',str(project_root(root)),settings.image,settings.cpu,settings.memory,function.__qualname__,inputs,semantic_args])
            session=create_session(root,settings=settings,cache_key=cache_key)
            transfer=WorkspaceTransfer(fs,session,settings,compiler=True,verified_snapshot=verified_snapshot,input_names=input_names)
            records=workspace_path(project_root(root),'.system/compiler-receipts/'+cache_key)
            state={'session':session,'settings':settings,'transfer':transfer,'root':root,'records':records,'ordinals':{}}
            token=None
            try:
                await asyncio.wait_for(session.start(),settings.startup_timeout)
                token=_ACTIVE.set(state)
                return await function(root,*args,**kwargs)
            except Exception as exc:
                detail=str(exc).replace(settings.api_key,'[redacted]') if settings.api_key else str(exc)
                raise RuntimeError(detail[:settings.max_log_bytes]) from None
            finally:
                if token is not None:_ACTIVE.reset(token)
                try:
                    await session.close()
                except Exception:
                    raise RuntimeError('沙箱销毁未确认；持久租约将阻止后续修改直至 TTL 到期') from None
    return wrapped


async def run_command(cmd,cwd,stdin=None,timeout=180):
    if _ACTIVE.get() is None:
        @sandbox_operation
        async def once(root):return await run_command(cmd,root,stdin,timeout)
        return await once(cwd)
    state=_ACTIVE.get();session=state['session'];settings=state['settings'];transfer=state['transfer'];root=state['root']
    if Path(cwd).absolute()!=root:raise ValueError('Compiler cwd changed outside its snapshot')
    if not cmd or cmd[0] not in {'pandoc','xelatex','bibtex'}:raise ValueError('Unsupported compiler command')
    versions=await transfer.sync_inputs()
    # Receipt identity uses the legacy logical root, independent of the adapter's
    # actual mount point. Otherwise switching roots could bypass an old receipt.
    argv=[str(value).replace(str(root),'/workspace') for value in cmd]
    remote_argv=[str(value).replace(str(root),session.root) for value in cmd]
    phase=_digest(argv)
    index=state['ordinals'].get(phase,0);state['ordinals'][phase]=index+1
    fingerprint=_digest([argv,stdin,versions,index])
    journal=state['records']/(phase+'-'+str(index)+'-'+fingerprint+'.json')
    record=json.loads(journal.read_text()) if journal.exists() else None
    if record is not None and record.get('provider','opensandbox') != settings.provider:
        raise RuntimeError('原编译记录属于另一沙箱平台；不会跨平台复用结果或自动重跑')
    if record is not None and record['status']=='submitted':
        raise RuntimeError('上次编译缺少完成回执，不会自动重跑；请核对原记录或修订源码后重新编译')
    if record is None:
        baseline={r['path']:r['sha256'] for r in await session.inventory(kind='compiler')}
        record={'id':'command_'+uuid.uuid4().hex,'status':'submitted','cache_key':session.cache_key,'provider':settings.provider,
                'input_versions':versions,'baseline':baseline,'argv':argv,'output_manifest':None}
        _save(journal,record)
        try:
            code,out,err=await asyncio.wait_for(session.exec(remote_argv,stdin=stdin,timeout=timeout),timeout+15)
            record.update(status='finished',exit_code=code,stdout=out,stderr=err,delivery_error='编译已完成，文件交付待确认')
            # This receipt is committed before any fallible file transfer.
            _save(journal,record)
        except BaseException as exc:
            detail=str(exc).replace(settings.api_key,'[redacted]') if settings.api_key else str(exc)
            record['error']=detail[:8000];_save(journal,record)
            raise
        fresh=True
    else:fresh=False
    try:
        manifest=record.get('output_manifest')
        if manifest is None:
            # A reconstructed container may only use an already frozen output version.
            manifest=await transfer.obtain_manifest(record['id'],record['baseline'],recover=not fresh)
            record['output_manifest']=manifest;_save(journal,record)
        for row in transfer.validate(manifest,record['id'])['files']:
            dest=transfer.fs.abs(row['path'])
            from .file_limits import bounded_hash
            if not dest.exists() or bounded_hash(dest,settings.max_file_bytes)!=row['sha256']:
                await transfer.download(row,dest)
        record['delivery_error']=None;_save(journal,record)
    except BaseException as exc:
        detail=str(exc).replace(settings.api_key,'[redacted]') if settings.api_key else str(exc)
        record['delivery_error']=detail[:8000];_save(journal,record)
        raise
    log=transfer.fs.abs('.build/commands/'+record['id']+'.log')
    atomic_write(log,lambda f:f.write((f"exit_code={record['exit_code']}\n"+record['stdout']+'\n'+record['stderr']).encode()))
    return record['exit_code'],record['stdout'],record['stderr']


def restore_reference(workspace, target):
    state=_ACTIVE.get()
    if state is None or state['root']!=Path(workspace).absolute():return False
    saved=state['records']/'styled-reference.docx'
    if not saved.exists():return False
    from .file_limits import bounded_copy
    atomic_write(target,lambda f:bounded_copy(saved,f,state['settings'].max_file_bytes))
    return True


def save_reference(workspace, target):
    state=_ACTIVE.get()
    if state is None or state['root']!=Path(workspace).absolute():return
    from .file_limits import bounded_copy
    atomic_write(state['records']/'styled-reference.docx',lambda f:bounded_copy(target,f,state['settings'].max_file_bytes))
