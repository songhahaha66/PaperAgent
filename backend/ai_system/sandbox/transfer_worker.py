"""Trusted file operations copied into the sandbox; no backend imports or secrets."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path('/workspace')
PRIVATE = {'.system', 'runs', 'manifest.json', 'chat_history.json', 'chat_history.json.lock'}


def path(value, internal=False):
    parts = value.split('/')
    if not value or value.startswith('/') or '\\' in value or '\x00' in value or re.match(r'^[A-Za-z]:', value) or any(p in {'', '.', '..'} for p in parts):
        raise ValueError('Invalid cache path')
    if not internal and any(p in PRIVATE or p.startswith('.pa-') for p in parts):
        raise ValueError('Private cache path')
    target = ROOT
    for part in parts:
        target = target / part
        if target.is_symlink():
            raise ValueError('Cache symbolic links are forbidden')
    if not target.resolve().is_relative_to(ROOT):
        raise ValueError('Cache path escaped workspace')
    return target


def info(name, limit):
    source = path(name)
    if not source.exists():
        return None
    if not source.is_file() or source.stat().st_size > limit:
        raise ValueError('文件超过单文件上限或不是普通文件: ' + name)
    digest = hashlib.sha256(); size = 0
    with source.open('rb') as stream:
        while chunk := stream.read(min(1024 * 1024, limit - size + 1)):
            size += len(chunk)
            if size > limit:
                raise ValueError('文件读取超过单文件上限: ' + name)
            digest.update(chunk)
    return {'path':name, 'size':size, 'sha256':digest.hexdigest()}


def inventory(kind, names, limit):
    if names is None:
        roots = {'python':['outputs','code'], 'runtime':[], 'inputs':['data','attachment','code','outputs/intermediate'],
                 'compiler':['.build/latex','.build/reference.docx','.build/export.docx']}[kind]
        if kind in {'python','runtime'}:
            roots = [p.name for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith('.')
                     and p.name not in PRIVATE | {'__pycache__','node_modules'}
                     and (kind=='runtime' or p.name not in {'data','attachment'})]
        names = []
        for name in roots:
            base = path(name)
            if base.is_symlink(): raise ValueError('Cache symlink')
            if base.is_file(): names.append(name)
            elif base.is_dir():
                for folder, dirs, files in os.walk(base, followlinks=False):
                    for child in dirs:
                        if (Path(folder)/child).is_symlink(): raise ValueError('Cache symlink')
                    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in PRIVATE | {'__pycache__','node_modules'}
                               and not (kind in {'python','runtime'} and Path(folder)==ROOT/'outputs' and d=='research')]
                    names.extend((Path(folder)/f).relative_to(ROOT).as_posix() for f in files if not f.startswith('.'))
        if kind in {'python','runtime'}:
            names += [p.name for p in ROOT.iterdir() if p.is_file() and not p.name.startswith('.')
                      and p.name not in PRIVATE | {'paper.md','paper.tex','paper.pdf','paper.docx'}]
    if len(names) > 10000: raise ValueError('File inventory exceeds 10000 entries')
    return [row for name in sorted(set(names)) if (row := info(name, limit)) is not None]


def freeze(run_id, baseline, kind, limit, names=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', run_id): raise ValueError('Invalid run ID')
    target = path('.pa-runs/' + run_id, True)
    receipt = target/'manifest.json'
    if receipt.exists(): return json.loads(receipt.read_text())
    rows = [row for row in inventory(kind, names, limit) if baseline.get(row['path']) != row['sha256']]
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.freeze-', dir=target.parent))
    try:
        for row in rows:
            source = path(row['path']); dest = temporary/'files'/row['path']; dest.parent.mkdir(parents=True,exist_ok=True)
            blob = path('.pa-blobs/' + row['sha256'], True)
            blob.parent.mkdir(parents=True,exist_ok=True)
            count = 0; digest = hashlib.sha256()
            staged = temporary / ('blob-' + row['sha256'])
            with source.open('rb') as incoming, staged.open('wb') as outgoing:
                while chunk := incoming.read(min(1024*1024, limit-count+1)):
                    count += len(chunk)
                    if count > limit: raise ValueError('产物超过单文件上限')
                    digest.update(chunk); outgoing.write(chunk)
            if count != row['size'] or digest.hexdigest() != row['sha256']: raise ValueError('Output changed while freezing')
            if blob.exists():
                if blob.stat().st_size != count or hashlib.sha256(blob.read_bytes()).hexdigest() != row['sha256']:
                    raise ValueError('Frozen content changed')
            else:
                staged.replace(blob)
            staged.unlink(missing_ok=True)
            os.link(blob,dest)
            if kind=='runtime' and row['path'].split('/')[0] in {'data','attachment'}:
                row['role']='runtime_input'
            row['snapshot'] = '.pa-runs/' + run_id + '/files/' + row['path']
        manifest = {'run_id':run_id,'files':rows}
        (temporary/'manifest.json').write_text(json.dumps(manifest))
        temporary.rename(target)
        return manifest
    finally:
        if temporary.exists(): shutil.rmtree(temporary)


def operation(request):
    kind=request.get('kind','python'); limit=request['limit']; op=request['op']
    if op=='inputs':
        ledger=path('.pa-inputs.json',True)
        if not ledger.exists():return {}
        if ledger.stat().st_size>8*1024*1024:raise ValueError('Input ledger too large')
        return json.loads(ledger.read_text())
    if op=='commit_inputs':
        source=path(request['temporary'],True)
        if source.stat().st_size>8*1024*1024:raise ValueError('Input ledger too large')
        data=json.loads(source.read_text())
        for name in data:path(name)
        os.replace(source,path('.pa-inputs.json',True))
        return None
    if op=='delete':
        path(request['path']).unlink(missing_ok=True)
        return None
    if op=='inventory': return inventory(kind,request.get('paths'),limit)
    if op=='freeze': return freeze(request['run_id'],request['baseline'],kind,limit,request.get('paths'))
    if op=='install':
        source=path(request['temporary'],True); target=path(request['path']); target.parent.mkdir(parents=True,exist_ok=True)
        # Temporary uploads are never treated as input cache hits.
        digest=hashlib.sha256();size=0
        with source.open('rb') as stream:
            while chunk:=stream.read(min(1024*1024,limit-size+1)):
                size+=len(chunk)
                if size>limit:raise ValueError('上传超过单文件上限')
                digest.update(chunk)
        if digest.hexdigest()!=request['sha256']:raise ValueError('Upload checksum mismatch')
        os.replace(source,target)
        return {'sha256':digest.hexdigest(),'size':size}
    raise ValueError('Unknown cache operation')


if __name__=='__main__':
    print(json.dumps(operation(json.loads(sys.argv[1]))))
