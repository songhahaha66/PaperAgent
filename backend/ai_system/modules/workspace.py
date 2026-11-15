"""Agent edits for scientific source and parameter files."""
import ast
import hashlib
from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from .file_edits import TextEdit, edited_text
from .registry import Inputs, CapabilityProduct
from services.file_services.workspace_files import public_parts
from services.file_services.workspace_layout import safe_join
from services.file_services.workspace_lock import edit_lock


class FileEdit(Inputs):
    path: str = Field(description='code/ 下的源码或 config/ 下的参数文件')
    content: str | None = None
    edits: list[TextEdit] = Field(default_factory=list, description='对同一文件集中应用的唯一原文替换')
    expected_version: SkipJsonSchema[str | None] = None


def source_path(root, name):
    parts = public_parts(name)
    if len(parts) < 2 or parts[0] not in {'code', 'config'}:
        raise ValueError('计算源码和参数请放在 code/ 或 config/；正文使用 writing_edit')
    return safe_join(root, name)


async def edit(ctx, inputs):
    from ai_system.research.models import ResearchConflict
    name = inputs['path']
    path = source_path(ctx.workspace, name)
    with edit_lock(ctx.workspace):
        raw = path.read_bytes() if path.exists() else None
        if raw is not None and inputs['expected_version'] != hashlib.sha256(raw).hexdigest():
            raise ResearchConflict('文件已存在或发生变化，请先读取当前文件再修改')
        text = edited_text(raw.decode('utf-8') if raw is not None else '',
                           inputs['content'], None, inputs['edits'])
        if path.suffix == '.py':
            try:
                ast.parse(text, filename=name)
            except SyntaxError as exc:
                raise ValueError(f'{name}:{exc.lineno}: {exc.msg}') from None
        ctx.fs.write_text(name, text, kind='code' if name.startswith('code/') else 'data', created_by='ai')
    version = hashlib.sha256(text.encode('utf-8')).hexdigest()
    await ctx.events.file_changed(name)
    return CapabilityProduct('research_state', {'path': name, 'version': version, 'size': len(text.encode('utf-8'))})


def register(registry):
    registry.register('workspace', 'workspace.edit', 'research_state', edit, FileEdit, model_callable=True,
        purpose='直接创建或修改 code/ 源码和 config/ 参数文件；已有文件先读取，自动绑定版本。支持完整 content 或多处 edits；保存代码不执行 Python。')
