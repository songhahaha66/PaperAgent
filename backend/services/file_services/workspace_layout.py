"""One persistent public tree; backend state and evidence are never sandbox mounts."""
import hashlib
import os
import re
from pathlib import Path, PurePosixPath
from config.paths import get_pa_data_base

PRIVATE_NAMES = {'.system', 'runs', 'manifest.json', 'chat_history.json', 'chat_history.json.lock'}


def relative_parts(value):
    value = str(value)
    parts = value.split('/')
    if not value or value.startswith('/') or '\\' in value or '\x00' in value or any(p in {'', '.', '..'} for p in parts) or re.match(r'^[A-Za-z]:', value):
        raise ValueError('Invalid relative path')
    return parts


def safe_join(root, relative):
    root = Path(root).absolute()
    parts = relative_parts(relative)
    # Normalize and check the SAME value we return. Include the separator so
    # /project-other cannot pass a /project boundary check. stdlib normalization
    # and startswith are also understood by CodeQL's path-injection analysis.
    prefix = os.path.realpath(root).rstrip(os.sep) + os.sep
    lexical = os.path.abspath(os.path.join(root, *parts))
    if not lexical.startswith(prefix):
        raise ValueError('Invalid path')
    resolved = os.path.realpath(lexical)
    if not resolved.startswith(prefix):
        raise ValueError('Invalid path')
    # Inspect lexical components as well: even in-tree links are forbidden.
    target = Path(lexical)
    for parent in [root, *root.parents, target, *target.parents]:
        if parent.is_symlink():
            raise ValueError('Symbolic links are not allowed')
    return Path(resolved)


def private_root(root):
    key = hashlib.sha256(str(Path(root).absolute()).encode()).hexdigest()
    return get_pa_data_base() / 'project-state' / key


def evidence_root(root):
    return get_pa_data_base() / 'evidence' / private_root(root).name


def workspace_path(root, relative):
    """Trusted internal resolver; API callers use WorkspaceFileService.resolve."""
    parts = relative_parts(relative)
    for index, part in enumerate(parts):
        if part in PRIVATE_NAMES:
            public = safe_join(root, '/'.join(parts[:index])) if index else Path(root)
            return safe_join(evidence_root(public) if part == 'runs' else private_root(public), '/'.join(parts[index:]))
    return safe_join(root, relative)
