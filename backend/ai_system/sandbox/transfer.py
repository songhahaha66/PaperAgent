"""Single-direction input versions and verified delivery between independent filesystems."""
import os
from pathlib import Path
import re
import tempfile

from services.file_services.file_helper import atomic_write
from services.file_services.workspace_layout import safe_join, relative_parts, PRIVATE_NAMES
from .file_limits import bounded_hash, bounded_copy


def relative(path, *, compiler=False):
    parts=relative_parts(path)
    if any(p in PRIVATE_NAMES or p.startswith('.pa-') for p in parts):raise ValueError('Private transfer path')
    if any(p.startswith('.') for p in parts) and not (compiler and parts[0]=='.build' and all(not p.startswith('.') for p in parts[1:])):
        raise ValueError('Hidden transfer path')
    return '/'.join(parts)


def output_path(path, *, compiler=False):
    path=relative(path,compiler=compiler)
    if compiler:
        if path not in {'.build/reference.docx','.build/export.docx'} and not path.startswith('.build/latex/'):
            raise ValueError('Compiler output is outside its delivery directories')
    elif path.split('/')[0] in {'data','attachment','paper.md','paper.tex','paper.pdf','paper.docx'} or path.startswith('outputs/research/'):
        raise ValueError('Execution output cannot replace inputs or formal evidence')
    return path


def input_files(fs, *, compiler=False, verified_snapshot=False, names=None):
    if names is not None:
        found={}
        for name in names:
            name=relative(name,compiler=compiler)
            found[name]=fs.canonical_artifact(name) if name.startswith('outputs/research/') and not verified_snapshot else safe_join(fs.root,name)
        if compiler and (fs.root/'.build/reference.docx').exists():found['.build/reference.docx']=safe_join(fs.root,'.build/reference.docx')
        return found
    roots=[fs.root] if compiler else [fs.abs(p) for p in ('data','attachment','code','outputs/intermediate')]
    found={}
    for root in roots:
        if not root.exists():continue
        for directory, dirs, files in os.walk(root,followlinks=False):
            for name in dirs:
                if (Path(directory)/name).is_symlink():raise ValueError('Input symlink')
            dirs[:]=[d for d in dirs if not d.startswith('.') and d not in {'experiments','runs','node_modules','__pycache__'}]
            for name in files:
                path=Path(directory)/name; rel=path.relative_to(fs.root).as_posix()
                if name.startswith('.') or name in PRIVATE_NAMES or (compiler and rel in {'paper.pdf','paper.docx'}):continue
                relative(rel,compiler=compiler)
                found[rel]=fs.canonical_artifact(rel) if compiler and rel.startswith('outputs/research/') and not verified_snapshot else safe_join(fs.root,rel)
    if compiler and (fs.root/'.build/reference.docx').exists():found['.build/reference.docx']=safe_join(fs.root,'.build/reference.docx')
    if not compiler:
        # Explicit predecessor handoffs outside intermediate/ are already scoped by the worker.
        for rel,meta in fs._manifest.get('files',{}).items():
            if meta.get('source_run') and not rel.startswith(('runs/','outputs/research/')):
                relative(rel);source=fs.abs(rel)
                if source.is_file():found[rel]=source
    return found


class WorkspaceTransfer:
    def __init__(self, fs, session, settings, *, compiler=False, verified_snapshot=False, input_names=None):
        self.input_names=input_names
        self.verified_snapshot=verified_snapshot
        self.fs=fs;self.session=session;self.settings=settings;self.compiler=compiler
        self.seen=None

    def local_inputs(self):
        return {p:bounded_hash(source,self.settings.max_file_bytes) for p,source in input_files(self.fs,compiler=self.compiler,verified_snapshot=self.verified_snapshot,names=self.input_names).items()}

    async def sync_inputs(self):
        sources=input_files(self.fs,compiler=self.compiler,verified_snapshot=self.verified_snapshot,names=self.input_names)
        desired={p:bounded_hash(source,self.settings.max_file_bytes) for p,source in sources.items()}
        previous=self.seen if self.seen is not None else await self.session.input_versions()
        check=desired if self.seen is None else {p:h for p,h in desired.items() if self.seen.get(p)!=h}
        actual={row['path']:row['sha256'] for row in await self.session.inventory(list(check),kind='compiler' if self.compiler else 'python')} if check else {}
        for path,digest in check.items():
            relative(path,compiler=self.compiler)
            if actual.get(path)!=digest:
                await self.session.upload(path,sources[path],digest)
        for path in set(previous)-set(desired):
            relative(path,compiler=self.compiler)
            await self.session.delete_input(path)
        await self.session.save_input_versions(desired)
        self.seen=desired
        return desired

    async def freeze_actual_inputs(self, run_id):
        """Retain live remote input mutations without merging them into user files."""
        info=self.fs.get_run_info(run_id)
        rows=await self.session.inventory(kind='inputs')
        extra=await self.session.inventory(list(self.seen or {})) if self.seen else []
        actual={row['path']:row['sha256'] for row in rows+extra}
        for name in actual:relative(name)
        baseline=info.get('input_hashes',{})
        snapshot_id='input_'+run_id
        manifest=await self.session.capture_inputs(snapshot_id,baseline,list(actual))
        if manifest.get('run_id')!=snapshot_id:raise ValueError('Input receipt mismatch')
        received=set()
        for row in manifest['files']:
            name=relative(row['path'])
            if name not in actual or name in received or row['sha256']!=actual[name]:raise ValueError('Input snapshot mismatch')
            if row['snapshot']!='.pa-runs/'+snapshot_id+'/files/'+name:raise ValueError('Input snapshot escaped its run')
            if not isinstance(row['size'],int) or not 0<=row['size']<=self.settings.max_file_bytes:raise ValueError('Input exceeds single-file limit')
            received.add(name)
            await self.download(row,self.fs.run_dir(run_id)/'inputs'/name)
        if received!={name for name,digest in actual.items() if baseline.get(name)!=digest}:raise ValueError('Incomplete input snapshot')
        # Locally frozen files removed by prior live code are not actual inputs.
        for name in set(baseline)-set(actual):
            safe_join(self.fs.run_dir(run_id)/'inputs',name).unlink(missing_ok=True)
        info['input_hashes']=actual
        self.fs._save_manifest()

    def validate(self, manifest, run_id):
        if manifest.get('run_id')!=run_id or not isinstance(manifest.get('files'),list):raise ValueError('Output receipt mismatch')
        seen=set()
        for row in manifest['files']:
            path=output_path(row['path'],compiler=self.compiler)
            if path in seen:raise ValueError('Duplicate output path')
            seen.add(path)
            if not isinstance(row['size'],int) or not 0<=row['size']<=self.settings.max_file_bytes:raise ValueError('Output exceeds single-file limit')
            if not re.fullmatch(r'[a-f0-9]{64}',row['sha256']):raise ValueError('Invalid output checksum')
            if row['snapshot']!='.pa-runs/'+run_id+'/files/'+path:raise ValueError('Output snapshot escaped its run')
        return manifest

    async def obtain_manifest(self, run_id, baseline, *, recover=False):
        if recover:
            manifest=await self.session.frozen(run_id)
        else:
            manifest=await self.session.capture(run_id,baseline,kind='compiler' if self.compiler else 'python')
        return self.validate(manifest,run_id)

    async def download(self, row, target):
        target.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='.download-',dir=target.parent);os.close(fd)
        temporary=Path(name)
        try:
            await self.session.download(row['snapshot'],temporary,self.settings.max_file_bytes)
            if temporary.stat().st_size!=row['size'] or bounded_hash(temporary,self.settings.max_file_bytes)!=row['sha256']:
                raise ValueError('Output size/checksum mismatch; original file preserved')
            # Commit through the pinned directory implementation, not a remotely controlled path.
            atomic_write(target,lambda f:bounded_copy(temporary,f,self.settings.max_file_bytes))
        finally:temporary.unlink(missing_ok=True)

    async def deliver(self, run_id, manifest, local_baseline):
        self.validate(manifest,run_id)
        info=self.fs.get_run_info(run_id)
        files=[]
        for row in manifest['files']:
            path=row['path'];dest=safe_join(self.fs.root,path)
            archive=f'runs/{run_id}/artifacts/{path}';target=self.fs.abs(archive)
            if target.exists():
                if bounded_hash(target,self.settings.max_file_bytes)!=row['sha256']:raise ValueError('Cannot overwrite a different archived result')
            else:await self.download(row,target)
            self.fs.register_run_artifact(run_id,path)
            current=bounded_hash(dest,self.settings.max_file_bytes) if dest.exists() else None
            if current not in {local_baseline.get(path),row['sha256']}:
                raise ValueError('本地工作副本已修改，归档保留；拒绝用远端结果覆盖人工修改')
            if current!=row['sha256']:
                atomic_write(dest,lambda f:bounded_copy(target,f,self.settings.max_file_bytes))
            self.fs.register_file(path,source_run=run_id,kind='code' if path.endswith('.py') else 'data')
            self.fs._manifest['files'][path].update(archive_path=archive,sha256=row['sha256'])
            files.append(archive)
            info['files']=list(files);self.fs._save_manifest()
        return files
