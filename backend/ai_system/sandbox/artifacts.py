"""Publish captured execution files without promoting them to scientific evidence."""
import shutil
from pathlib import Path, PurePosixPath


def publish_run_files(source_fs, target_fs, attempt_id):
    if source_fs.root == target_fs.root:
        return []
    published = []
    for run_id, info in source_fs._manifest.get('runs', {}).items():
        for rel in info.get('artifacts', []):
            prefix = f'runs/{run_id}/artifacts/'
            if not rel.startswith(prefix):
                raise ValueError('运行产物不属于对应 run')
            name = rel.removeprefix(prefix)
            parts = PurePosixPath(name).parts
            if not parts or any(part.startswith('.') for part in parts) or '\\' in name:
                raise ValueError('非法运行产物路径')
            source = source_fs.abs(rel)
            if source.is_symlink() or not source.is_file():
                raise ValueError('运行产物不存在或为符号链接')
            dest = f'outputs/research/{run_id}/{name}'
            target = target_fs.abs(dest)
            if target_fs.get_file_meta(dest) and target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            kind = 'image' if source.suffix.lower() in {'.png','.jpg','.jpeg','.svg'} else 'code' if source.suffix == '.py' else 'data'
            target_fs.register_file(dest, kind=kind, source_run=run_id, visibility='user')
            published.append(dest)
    return published


def capture_run_files(source_fs, target_fs, attempt_id=None):
    """Single publication boundary; inherited runs keep their identity and location."""
    if source_fs.root != target_fs.root:
        for run_id, info in source_fs._manifest.get('runs', {}).items():
            source = source_fs.run_dir(run_id)
            target = target_fs.run_dir(run_id)
            if source.exists():
                shutil.copytree(source, target, dirs_exist_ok=True)
            target_fs._manifest.setdefault('runs', {})[run_id] = dict(info)
        target_fs._save_manifest()
    return publish_run_files(source_fs, target_fs, attempt_id)


def publish_reference(source_fs, target_fs, item):
    """Resolve a figure to its immutable run file; no experiment-specific renaming."""
    if not item.get('path'):
        return
    run_id = item['run_id']
    info = source_fs.get_run_info(run_id) or {}
    import hashlib
    source = source_fs.abs(item['path'])
    if not source.is_file():
        raise FileNotFoundError('引用文件不存在：' + item['path'])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    for rel in info.get('artifacts', []):
        original = source_fs.abs(rel)
        if original.is_file() and hashlib.sha256(original.read_bytes()).hexdigest() == digest:
            item['path'] = 'outputs/research/' + run_id + '/' + rel.split('/artifacts/', 1)[1]
            return
    # Compatibility for older results without an immutable run inventory.
    dest = f"outputs/research/{run_id}/{source.name}"
    target = target_fs.abs(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target_fs.register_file(dest, kind='image', source_run=run_id)
    item['path'] = dest


def collect_references(ctx, subproblem_id, result):
    """Consume only explicit structured output and actual figures from a successful run."""
    if not result.ok:
        return []
    import json
    refs = []
    info = ctx.fs.get_run_info(result.run_id) or {}
    output = ctx.fs.read_text(info['stdout']) if info.get('stdout') else result.stdout
    for line in output.splitlines():
        if not line.startswith('PAPERAGENT_RESULT '):
            continue
        try:
            item = json.loads(line.removeprefix('PAPERAGENT_RESULT '))
            value = item['value']
            value = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value)
            ref = ctx.registry.record_value(subproblem_id, item['name'], value, result.run_id,
                item.get('unit', ''), item.get('description', ''), item.get('meaning'))
            refs.append(ref)
        except (ValueError, TypeError, KeyError) as exc:
            # A malformed declaration never changes the computation's outcome.
            refs.append(str(exc))
    for figure in result.figures:
        try:
            ref = ctx.registry.record_figure(subproblem_id, result.run_id, figure, Path(figure).stem)
            refs.append(ref)
        except (ValueError, OSError) as exc:
            refs.append('图片引用尚未就绪：' + str(exc))
    return refs


def output_hashes(fs):
    import hashlib
    result = {}
    paths = [p for folder in ('outputs', 'code') for p in fs.abs(folder).rglob('*')]
    paths += [p for p in fs.root.iterdir() if p.is_file() and not p.name.startswith('.') and
              p.name not in {'manifest.json','chat_history.json','chat_history.json.lock','paper.tex','paper.md','paper.pdf'}]
    for path in sorted(paths):
        if path.is_symlink(): raise ValueError('输出不允许符号链接')
        if path.is_file() and '__pycache__' not in path.parts:
            with path.open('rb') as stream:
                result[fs.rel(path)] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def capture_local_outputs(fs, run_id, before):
    saved = []
    for path, checksum in output_hashes(fs).items():
        if before.get(path) == checksum: continue
        target = fs.abs(f'runs/{run_id}/artifacts/{path}')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fs.abs(path), target)
        fs.register_run_artifact(run_id, path)
        fs.register_file(path, source_run=run_id, kind='code' if path.endswith('.py') else 'data')
        saved.append(path)
    return saved
