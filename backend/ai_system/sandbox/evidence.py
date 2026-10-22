"""Selective run receipts stored outside writable sandbox mounts."""
import hashlib
import json
from services.file_services.workspace_layout import safe_join
from services.file_services.file_helper import atomic_write, open_read
from .file_limits import DEFAULT_MAX_FILE_BYTES, bounded_copy, bounded_hash


def checksum(path):
    with open_read(path) as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def freeze_inputs(fs, run_id, *, max_file_bytes=DEFAULT_MAX_FILE_BYTES):
    info = fs.get_run_info(run_id)
    frozen = {}
    # Attempt inputs are already the task-selected inputs. Do not copy outputs or project history.
    for folder in ('data', 'attachment', 'code', 'outputs/intermediate'):
        source = fs.abs(folder)
        if not source.exists():
            continue
        for path in source.rglob('*'):
            relative = path.relative_to(fs.root).as_posix()
            path = safe_join(fs.root, relative)
            if not path.is_file():
                continue
            target = fs.run_dir(run_id) / 'inputs' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target, lambda f, p=path: bounded_copy(p, f, max_file_bytes))
            frozen[relative] = bounded_hash(target, max_file_bytes)
            if frozen[relative] != bounded_hash(path, max_file_bytes):
                raise ValueError('冻结输入期间文件发生变化，拒绝执行')
    info['input_hashes'] = frozen
    fs._save_manifest()


def seal_run(fs, run_id):
    info = fs.get_run_info(run_id)
    if info.get('status') not in {'success', 'error'}:
        raise ValueError('运行缺少真实完成回执，不能归档证据')
    hashes = {}
    root = fs.run_dir(run_id)
    for path in root.rglob('*'):
        relative = path.relative_to(root).as_posix()
        path = safe_join(root, relative)
        if path.is_file() and relative != 'receipt.json':
            hashes[relative] = checksum(path)
    receipt = {'run_id': run_id, 'status': info['status'], 'files': hashes,
               'input_hashes': info.get('input_hashes', {}), 'runtime_image': info.get('runtime_image')}
    target = root / 'receipt.json'
    if target.exists() and json.loads(target.read_text()) != receipt:
        raise ValueError('已归档运行版本不一致，拒绝覆盖')
    if not target.exists():
        atomic_write(target, lambda f: f.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode()))
    info['evidence_archived'] = True
    fs._save_manifest()


def verify_run(fs, run_id):
    info = fs.get_run_info(run_id) or {}
    if not info.get('evidence_archived'):
        raise ValueError('运行证据未完整归档，不能登记结果')
    receipt = json.loads((fs.run_dir(run_id) / 'receipt.json').read_text())
    if receipt['run_id'] != run_id or receipt['status'] != 'success':
        raise ValueError('证据回执不匹配')
    for path, expected in receipt['files'].items():
        if checksum(safe_join(fs.run_dir(run_id), path)) != expected:
            raise ValueError('证据文件校验失败：' + path)
