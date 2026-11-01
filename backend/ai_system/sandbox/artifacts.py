"""Publish captured execution files without promoting them to scientific evidence."""
from .file_limits import DEFAULT_MAX_FILE_BYTES, bounded_hash, bounded_copy
from services.file_services.workspace_layout import workspace_path
import shutil
from copy import deepcopy
from pathlib import Path, PurePosixPath


def publish_run_files(source_fs, target_fs, attempt_id=None, *, run_ids=None):
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
            if not existing or not target.is_file():
                atomic_write(target,lambda f:bounded_copy(source,f,DEFAULT_MAX_FILE_BYTES))
            kind = 'image' if source.suffix.lower() in {'.png','.jpg','.jpeg','.svg'} else 'code' if source.suffix == '.py' else 'data'
            target_fs.register_file(dest, kind=kind, source_run=run_id, visibility='user',
                                    archive_path=rel, sha256=expected)
            published.append(dest)
    return published


def _copy_run_archive(source, target, *, content=None):
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
    def copy(origin,destination):
        content.link_archive(content.put(Path(origin)),Path(destination))
        return destination
    shutil.copytree(source, target, dirs_exist_ok=True,copy_function=copy if content else shutil.copy2)
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
                from .content_store import ContentStore
                _copy_run_archive(source, target,content=ContentStore(target_fs.root,DEFAULT_MAX_FILE_BYTES))
            target_fs._manifest.setdefault('runs', {})[run_id] = deepcopy(info)
            for result_id, binding in source_fs._manifest.get('result_bindings', {}).items():
                if binding['run_id'] == run_id:
                    target_fs._manifest.setdefault('result_bindings', {})[result_id] = deepcopy(binding)
            changed = True
        if changed:
            target_fs._save_manifest()
    return publish_run_files(source_fs, target_fs, attempt_id, run_ids=selected)


def inherit_execution_files(source, target_fs, experiment_id, *, run_ids=None):
    """Restore one execution's own runs and handoffs, retaining immutable IDs."""
    from services.file_services.workspace_fs import WorkspaceFS
    manifest = workspace_path(source, 'manifest.json')
    if not manifest.is_file(): return set()
    source_fs = WorkspaceFS(str(source))
    inventory = source_fs._manifest
    selected = (set(inventory.get('runs', {})) - set(inventory.get('inherited_runs', []))
                if run_ids is None else set(run_ids))
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
                from .content_store import ContentStore
                _copy_run_archive(origin, target_fs.run_dir(run_id),content=ContentStore(target_fs.root,DEFAULT_MAX_FILE_BYTES))
            target_fs._manifest['runs'][run_id] = deepcopy(info)
        # Materialize original paths from immutable snapshots, even if the
        # previous attempt no longer has a mutable working copy.
        prefix = f'runs/{run_id}/artifacts/'
        for artifact in info.get('artifacts', []):
            if not artifact.startswith(prefix): raise ValueError('运行产物不属于对应 run')
            name = artifact.removeprefix(prefix)
            parts = PurePosixPath(name).parts
            if not parts or any(p.startswith('.') for p in parts) or parts[0] in {'manifest.json','chat_history.json'}:
                raise ValueError('非法实验交接路径')
            if parts[0] in {'data','attachment'}:
                # Generated input versions remain separate from original user
                # materials during cross-task handoff as well as publication.
                name='outputs/intermediate/imported/'+run_id+'/'+name
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


def output_hashes(fs, *, max_file_bytes=DEFAULT_MAX_FILE_BYTES):
    import os
    from .transfer import output_path
    result = {}
    from services.file_services.workspace_layout import PRIVATE_NAMES
    paths=[]
    for directory, dirs, files in os.walk(fs.root, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in PRIVATE_NAMES | {'__pycache__','node_modules'}
                   and not (Path(directory)==fs.root and d in {'data','attachment'})
                   and not (Path(directory)==fs.root/'outputs' and d=='research')]
        for name in files:
            path=Path(directory)/name
            if name.startswith('.') or name in PRIVATE_NAMES:continue
            try:output_path(path.relative_to(fs.root).as_posix())
            except ValueError:continue
            paths.append(path)
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
