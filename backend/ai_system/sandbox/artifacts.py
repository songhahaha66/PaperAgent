"""Publish captured execution files without promoting them to scientific evidence."""
from services.file_services.file_helper import open_read
from .file_limits import DEFAULT_MAX_FILE_BYTES, bounded_hash, bounded_copy
from services.file_services.workspace_layout import workspace_path
import shutil
from copy import deepcopy
from pathlib import Path, PurePosixPath


def publish_run_files(source_fs, target_fs, attempt_id=None, *, run_ids=None):
    if source_fs.root == target_fs.root:
        return []
    published = []
    runs = source_fs._manifest.get('runs', {})
    for run_id in runs if run_ids is None else run_ids:
        info = runs[run_id]
        if not info.get('evidence_archived') or info.get('status')!='success':continue
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
            from .evidence import checksum
            from services.file_services.file_helper import atomic_write
            expected=checksum(source)
            existing=target_fs.get_file_meta(dest)
            if existing:
                if existing.get('archive_path')!=rel or existing.get('sha256')!=expected:
                    raise ValueError('正式成果编号已绑定其他归档版本')
            else:
                atomic_write(target,lambda f:bounded_copy(source,f,DEFAULT_MAX_FILE_BYTES))
            kind = 'image' if source.suffix.lower() in {'.png','.jpg','.jpeg','.svg'} else 'code' if source.suffix == '.py' else 'data'
            target_fs.register_file(dest, kind=kind, source_run=run_id, visibility='user')
            target_fs._manifest['files'][dest].update(archive_path=rel,sha256=expected)
            target_fs._save_manifest()
            published.append(dest)
    return published


def _copy_run_archive(source, target):
    from .evidence import checksum
    from services.file_services.workspace_layout import safe_join
    import json
    receipt = source / 'receipt.json'
    existing = target / 'receipt.json'
    def validate(directory):
        data = json.loads((directory/'receipt.json').read_text())
        for name, expected in data['files'].items():
            if checksum(safe_join(directory,name)) != expected:
                raise ValueError('运行归档完整性检查失败')
    if existing.exists():
        if not receipt.exists() or checksum(receipt) != checksum(existing):
            raise ValueError('拒绝覆盖已归档运行的其他版本')
        validate(source)
        validate(target)
        return
    if receipt.exists():
        validate(source)
    if any(path.is_symlink() for path in source.rglob('*')):
        raise ValueError('运行归档不允许符号链接')
    shutil.copytree(source, target, dirs_exist_ok=True)
    if receipt.exists():
        validate(target)


def capture_run_files(source_fs, target_fs, attempt_id=None, *, run_ids=None):
    """Single publication boundary; inherited runs keep their identity and location."""
    runs = source_fs._manifest.get('runs', {})
    selected = list(runs if run_ids is None else run_ids)
    changed = False
    if source_fs.root != target_fs.root:
        for run_id in selected:
            info = runs[run_id]
            if info == target_fs.get_run_info(run_id) and target_fs.run_dir(run_id).exists():
                continue
            source = source_fs.run_dir(run_id)
            target = target_fs.run_dir(run_id)
            if source.exists():
                _copy_run_archive(source, target)
            target_fs._manifest.setdefault('runs', {})[run_id] = deepcopy(info)
            for result_id, binding in source_fs._manifest.get('result_bindings', {}).items():
                if binding['run_id'] == run_id:
                    target_fs._manifest.setdefault('result_bindings', {})[result_id] = deepcopy(binding)
            changed = True
        if changed:
            target_fs._save_manifest()
    return publish_run_files(source_fs, target_fs, attempt_id, run_ids=selected)


def inherit_execution_files(source, target_fs, experiment_id):
    """Restore one execution's own runs and handoffs, retaining immutable IDs."""
    from services.file_services.workspace_fs import WorkspaceFS
    manifest = workspace_path(source, 'manifest.json')
    if not manifest.is_file(): return set()
    source_fs = WorkspaceFS(str(source))
    inventory = source_fs._manifest
    selected = set(inventory.get('runs', {})) - set(inventory.get('inherited_runs', []))
    def copy_file(path):
        parts = PurePosixPath(path).parts
        if not parts or any(p.startswith('.') for p in parts) or parts[0] in {'manifest.json', 'chat_history.json', 'data', 'attachment'}:
            raise ValueError('非法实验交接路径')
        origin = source_fs.abs(path)
        target = target_fs.abs(path)
        if origin.is_symlink(): raise ValueError('实验交接不允许符号链接')
        if origin.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
    for run_id, info in inventory.get('runs', {}).items():
        if run_id not in selected: continue
        if run_id not in target_fs._manifest['runs']:
            origin = source_fs.run_dir(run_id)
            if origin.exists():
                if any(p.is_symlink() for p in origin.rglob('*')): raise ValueError('运行记录不允许符号链接')
                _copy_run_archive(origin, target_fs.run_dir(run_id))
            target_fs._manifest['runs'][run_id] = deepcopy(info)
        # Materialize original paths from immutable snapshots, even if the
        # previous attempt no longer has a mutable working copy.
        prefix = f'runs/{run_id}/artifacts/'
        for artifact in info.get('artifacts', []):
            if not artifact.startswith(prefix): raise ValueError('运行产物不属于对应 run')
            name = artifact.removeprefix(prefix)
            parts = PurePosixPath(name).parts
            if not parts or any(p.startswith('.') for p in parts) or parts[0] in {'data','attachment','manifest.json','chat_history.json'}:
                raise ValueError('非法实验交接路径')
            origin = source_fs.abs(artifact)
            target = target_fs.abs(name)
            if origin.is_symlink(): raise ValueError('运行产物不允许符号链接')
            if origin.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, target)
                target_fs._manifest['files'][name] = {'source_run':run_id, 'kind':'data', 'visibility':'user'}
    for path, meta in inventory.get('files', {}).items():
        if meta.get('source_run') in selected:
            copy_file(path)
            target_fs._manifest['files'][path] = deepcopy(meta)
    # The exported solution is produced outside the kernel, so has no run ID.
    copy_file(f'code/{experiment_id}_solution.py')
    target_fs._save_manifest()
    return selected


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
    raise ValueError('引用缺少已归档运行产物，不能登记')


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


def output_hashes(fs, *, max_file_bytes=DEFAULT_MAX_FILE_BYTES):
    result = {}
    paths = [p for folder in ('outputs', 'code') for p in fs.abs(folder).rglob('*')]
    paths += [p for p in fs.root.iterdir() if p.is_file() and not p.name.startswith('.') and
              p.name not in {'manifest.json','chat_history.json','chat_history.json.lock','paper.tex','paper.md','paper.pdf'}]
    for path in sorted(paths):
        from services.file_services.workspace_layout import safe_join
        path = safe_join(fs.root, path.relative_to(fs.root).as_posix())
        if path.is_symlink(): raise ValueError('输出不允许符号链接')
        if path.is_file() and '__pycache__' not in path.parts:
            result[fs.rel(path)] = bounded_hash(path, max_file_bytes)
    return result


def capture_local_outputs(fs, run_id, before, *, expected=None, max_file_bytes=DEFAULT_MAX_FILE_BYTES):
    saved = []
    for path, checksum in (expected if expected is not None else output_hashes(fs, max_file_bytes=max_file_bytes)).items():
        if expected is None and before.get(path) == checksum: continue
        target = fs.abs(f'runs/{run_id}/artifacts/{path}')
        target.parent.mkdir(parents=True, exist_ok=True)
        from services.file_services.file_helper import atomic_write
        if target.exists():
            if bounded_hash(target, max_file_bytes) != checksum:
                raise ValueError('已归档运行产物版本冲突')
        else:
            atomic_write(target, lambda f: bounded_copy(fs.abs(path), f, max_file_bytes))
        if bounded_hash(target, max_file_bytes) != checksum:
            target.unlink(missing_ok=True)
            raise ValueError('归档期间产物发生变化，保留未完成状态')
        fs.register_run_artifact(run_id, path)
        fs.register_file(path, source_run=run_id, kind='code' if path.endswith('.py') else 'data')
        saved.append(path)
    return saved
