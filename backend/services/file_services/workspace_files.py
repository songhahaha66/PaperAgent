"""Project-relative public file gateway. Storage is selected exclusively by the server."""
import base64
import json
import mimetypes
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from fastapi import HTTPException
from config.paths import get_workspaces_path, get_workspace_path, get_legacy_workspace_path
from .file_helper import FileHelper, open_read
from .workspace_layout import relative_parts, safe_join, workspace_path, PRIVATE_NAMES
from .workspace_lock import edit_lock

# Public research files; runtime databases, credentials, caches and checkpoints are excluded.
EXPORT_SUFFIXES = set('md txt csv tsv json jsonl parquet xlsx xls pdf docx tex bib sty cls bst def cfg bbx cbx py r m ipynb png jpg jpeg svg webp gif eps ttf otf zip log out'.split())
FORBIDDEN_NAMES = PRIVATE_NAMES | {'owner.json', 'metadata.json', 'credentials.json', 'token.json', 'config.json', '__pycache__', 'node_modules'}


def public_parts(path):
    parts = relative_parts(path)
    if any(p.startswith('.') or p in FORBIDDEN_NAMES for p in parts) or Path(path).suffix.lower() in {'.sqlite', '.sqlite3', '.db', '.pem', '.key'}:
        raise ValueError('Private workspace path')
    return parts


def safe_files(root):
    if not root.exists():
        return
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in list(dirs):
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError('Workspace contains symbolic links')
            if name.startswith('.') or name in FORBIDDEN_NAMES:
                dirs.remove(name)
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            try:
                public_parts(relative)
            except ValueError:
                continue
            yield safe_join(root, relative)


class WorkspaceFileService:
    def __init__(self):
        self.base_path = get_workspaces_path()

    def get_workspace_path(self, work_id):
        return get_workspace_path(work_id)

    def ensure_workspace_exists(self, work_id):
        root = self.get_workspace_path(work_id)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def resolve(self, work_id, path, *, reading=True):
        try:
            parts = relative_parts(path)
            root = self.get_workspace_path(work_id)
            if reading and path in {'.system/validation.json', '.system/consistency.json'}:
                return workspace_path(root, path)
            if parts[0] == '.build' and reading and Path(path).suffix.lower() in {'.log', '.blg'}:
                public_parts('/'.join(parts[1:]))
                return safe_join(root, path)
            if 'runs' in parts and reading:
                index = parts.index('runs')
                allowed = (index == 0 or parts[0] == 'experiments') and len(parts) >= index + 3
                if not allowed or parts[index+2] not in {'input.py','stdout.log','stderr.log','artifacts'}:
                    raise ValueError('Private run path')
                from .workspace_fs import WorkspaceFS
                run_root = safe_join(root, '/'.join(parts[:index])) if index else root
                fs = WorkspaceFS(str(run_root))
                if not fs.get_run_info(parts[index+1]):
                    raise ValueError('Unknown run')
                return workspace_path(root, path)
            public_parts(path)
            if path=='outputs/research' or path.startswith('outputs/research/'):
                if not reading:raise HTTPException(409,'正式成果只读；请复制为新文件后修改')
                from .workspace_fs import WorkspaceFS
                return WorkspaceFS(root).canonical_artifact(path)
            return safe_join(root,path)
        except ValueError as exc:
            raise HTTPException(400, 'Invalid workspace path') from exc

    def list_files(self, work_id, relative_path='', recursive=True):
        rows = [row for group in self.list_files_by_category(work_id).values() for row in group]
        if relative_path:
            self.resolve(work_id, relative_path)
            rows = [r for r in rows if r['path'].startswith(relative_path.rstrip('/') + '/')]
        return rows

    def list_files_by_category(self, work_id):
        root = self.get_workspace_path(work_id)
        result = {k: [] for k in ('code', 'outputs', 'papers', 'attachments', 'data', 'template')}
        for path in safe_files(root):
            relative = path.relative_to(root).as_posix()
            first = relative.split('/')[0]
            # Execution scratch is persisted but surfaced through run provenance.
            if first == 'experiments':
                continue
            category = 'attachments' if first == 'attachment' else first if first in result else 'papers'
            actual=path
            if relative.startswith('outputs/research/'):
                from .workspace_fs import WorkspaceFS
                actual=WorkspaceFS(root).canonical_artifact(relative)
            stat = actual.stat()
            result[category].append({'name':path.name,'type':'file','size':stat.st_size,'modified':stat.st_mtime,
                'path':relative,'category_path':relative.split('/',1)[-1],'category':category})
        for rows in result.values():
            rows.sort(key=lambda r:r['path'])
        return result

    def detect_file_type(self, file_path):
        mime = mimetypes.guess_type(file_path)[0] or ''
        if mime.startswith('image/'):
            return 'image'
        if mime.startswith('text/') or Path(file_path).suffix.lower() in {'.py','.tex','.bib','.json','.yaml','.yml','.toml','.csv','.r','.sty','.cls','.log'}:
            return 'text'
        return 'binary'

    def read_file(self, work_id, file_path):
        target = self.resolve(work_id, file_path)
        if not target.is_file():
            raise HTTPException(404, 'File not found')
        kind = self.detect_file_type(file_path)
        size = target.stat().st_size
        if kind in {'text','image'} and size > 10 * 1024 * 1024:
            raise HTTPException(400, 'File too large to preview')
        result = {'type':kind,'filename':target.name,'size':size}
        if kind in {'text', 'image'}:
            with open_read(target) as stream:
                raw = stream.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024:
                raise HTTPException(400, 'File too large to preview')
            result['content'] = raw.decode('utf-8') if kind == 'text' else base64.b64encode(raw).decode()
        else:
            result.update(mime_type=mimetypes.guess_type(file_path)[0] or 'application/octet-stream',
                          download_url=f'/api/workspace/{work_id}/download/{file_path}')
        return result

    def file_response(self, work_id, path, *, image=False):
        from fastapi.responses import FileResponse
        from starlette.background import BackgroundTask
        target = self.resolve(work_id, path)
        try:
            stream = open_read(target)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, 'File not found') from None
        fd = os.dup(stream.fileno())
        stream.close()
        mime = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        if image and not mime.startswith('image/'):
            os.close(fd)
            raise HTTPException(400, 'Not an image')
        # Keep the already-validated descriptor (including range requests).
        # macOS exposes descriptors in /dev/fd rather than Linux's /proc.
        descriptor_root = '/proc/self/fd' if Path('/proc/self/fd').is_dir() else '/dev/fd'
        return FileResponse(descriptor_root + '/' + str(fd), stat_result=os.fstat(fd),
            media_type=mime, filename=None if image else target.name,
            headers={'X-Content-Type-Options':'nosniff', 'Content-Security-Policy':"sandbox; default-src 'none'; style-src 'unsafe-inline'"},
            background=BackgroundTask(os.close, fd))

    def _editable(self, work_id):
        from services.chat_services.task_manager import task_manager
        if task_manager.get_running_task(work_id):
            raise HTTPException(409, '研究正在执行，请暂停后修改文件')
        return edit_lock(self.get_workspace_path(work_id))

    def write_file(self, work_id, file_path, content):
        self.resolve(work_id, file_path, reading=False)
        with self._editable(work_id):
            path = FileHelper(self.ensure_workspace_exists(work_id)).write_text(file_path, content)
        return {'message':'File written successfully','path':file_path,'size':Path(path).stat().st_size}

    def upload_file(self, work_id, file_path, file):
        self.resolve(work_id, file_path, reading=False)
        with self._editable(work_id):
            path = FileHelper(self.ensure_workspace_exists(work_id)).write_bytes_stream(file_path, file.file)
        return {'message':'File uploaded successfully','path':file_path,'filename':file.filename,'size':Path(path).stat().st_size}

    def delete_file(self, work_id, file_path):
        if file_path=='outputs':raise HTTPException(409,'请逐项删除工作文件；正式成果只读')
        self.resolve(work_id, file_path, reading=False)
        with self._editable(work_id):
            FileHelper(self.get_workspace_path(work_id)).delete(file_path)
        return {'message':'File or directory deleted successfully'}

    def create_directory(self, work_id, dir_path):
        with self._editable(work_id):
            self.resolve(work_id, dir_path, reading=False).mkdir(parents=True, exist_ok=False)
        return {'message':'Directory created successfully'}

    def get_file_info(self, work_id, file_path):
        path = self.resolve(work_id, file_path)
        if not path.exists():
            raise HTTPException(404,'File not found')
        stat = path.stat()
        return {'name':path.name,'path':file_path,'type':'file' if path.is_file() else 'directory','size':stat.st_size,'modified':stat.st_mtime}

    def export_workspace(self, work_id, *, legacy=False):
        root = get_legacy_workspace_path(work_id) if legacy else self.get_workspace_path(work_id)
        folder = Path(tempfile.mkdtemp(prefix='paperagent-export-'))
        target = folder / f'workspace_{work_id}.zip'
        try:
            with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in safe_files(root):
                    if path.suffix.lower().lstrip('.') in EXPORT_SUFFIXES:
                        self._zip_file(archive, path if legacy else self.resolve(work_id,path.relative_to(root).as_posix()), path.relative_to(root).as_posix())
                # Historical attempts may contain the only copy of code/results.
                if legacy:
                    attempts = safe_join(root, '.system/attempts')
                    for path in safe_files(attempts):
                        if path.suffix.lower().lstrip('.') in EXPORT_SUFFIXES:
                            self._zip_file(archive, path, 'historical-attempts/' + path.relative_to(attempts).as_posix())
                run_base = safe_join(root, 'runs') if legacy else workspace_path(root, 'runs')
                if run_base.exists():
                    for run in run_base.iterdir():
                        if run.is_symlink() or not run.is_dir():
                            continue
                        for name in ('input.py', 'stdout.log', 'stderr.log'):
                            path = safe_join(run, name)
                            if path.is_file():
                                self._zip_file(archive, path, '运行记录/' + run.name + '/' + name)
                        for path in safe_files(run / 'artifacts'):
                            if path.suffix.lower().lstrip('.') in EXPORT_SUFFIXES:
                                self._zip_file(archive, path, '运行记录/' + run.name + '/artifacts/' + path.relative_to(run/'artifacts').as_posix())
                archive.writestr('研究记录.md', self._readable_records(root, legacy))
                archive.writestr('README.txt', '资料、源码、论文和成果导出。旧项目仅供阅读；新建项目上传资料不会恢复旧执行状态。数据库、checkpoint、凭证和原始模型追踪不在导出范围。')
            return str(target)
        except BaseException:
            shutil.rmtree(folder)
            raise

    @staticmethod
    def _zip_file(archive, path, name):
        with open_read(path) as source, archive.open(name, 'w') as target:
            shutil.copyfileobj(source, target)

    def _readable_records(self, root, legacy):
        path = safe_join(root, '.system/research.sqlite') if legacy else workspace_path(root, '.system/research.sqlite')
        lines = ['# 研究记录\n']
        if path.is_file():
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'research' in tables:
                    for (payload,) in db.execute('SELECT payload FROM research'):
                        state = json.loads(payload)
                        for name in ('spec','questions','experiments','artifacts','claims','validations','feedback','faults'):
                            if name in state:
                                lines.append('## ' + name + '\n\n```json\n' + json.dumps(state[name],ensure_ascii=False,indent=2) + '\n```\n')
                if 'chat_messages' in tables:
                    lines.append('## 对话\n')
                    for (payload,) in db.execute('SELECT payload FROM chat_messages ORDER BY sequence'):
                        message = json.loads(payload)
                        lines.append(str(message.get('role','')) + ': ' + str(message.get('content','')) + '\n')
        elif legacy:
            history = safe_join(root, 'chat_history.json')
            if history.is_file():
                data = json.loads(history.read_text())
                for row in data if isinstance(data,list) else data.get('messages',[]):
                    lines.append(str(row.get('role','')) + ': ' + str(row.get('content','')) + '\n')
        return '\n'.join(lines)


workspace_file_service = WorkspaceFileService()
