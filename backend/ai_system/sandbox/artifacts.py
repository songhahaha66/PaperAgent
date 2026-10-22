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
            dest = f'outputs/research/{attempt_id}/{run_id}/{name}'
            target = target_fs.abs(dest)
            if target_fs.get_file_meta(dest) and target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            kind = 'image' if source.suffix.lower() in {'.png','.jpg','.jpeg','.svg'} else 'code' if source.suffix == '.py' else 'data'
            target_fs.register_file(dest, kind=kind, source_run=run_id, visibility='user')
            published.append(dest)
    return published
