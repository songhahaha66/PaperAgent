import base64
import io
import uuid
from pathlib import Path
from config.paths import get_templates_path
from .file_helper import FileHelper
from .workspace_layout import relative_parts
from .archive_safety import validate_archive


class TemplateFileService:
    def __init__(self):
        self.base_path = get_templates_path()

    def save_file(self, template_id, filename, content, is_binary=False):
        if len(relative_parts(filename)) != 1:
            raise ValueError('Invalid template filename')
        raw = base64.b64decode(content, validate=True) if is_binary else content.encode('utf-8')
        if not raw or len(raw) > 10 * 1024 * 1024:
            raise ValueError('模板不能为空或超过 10MB')
        suffix = Path(filename).suffix.lower()
        if suffix not in {'.md','.tex','.docx'} or is_binary != (suffix == '.docx'):
            raise ValueError('模板类型不匹配')
        name = f'{template_id}_{uuid.uuid4().hex}{suffix}'
        helper = FileHelper(self.base_path)
        helper.write_bytes_stream(name, io.BytesIO(raw), max_bytes=10 * 1024 * 1024)
        try:
            if suffix == '.docx':
                validate_archive(helper.resolve(name))
        except BaseException:
            helper.delete(name)
            raise
        return name

    def get_text_content(self, filename):
        return FileHelper(self.base_path).read_text(filename)

    def delete_file(self, filename):
        path = FileHelper(self.base_path).resolve(filename)
        path.unlink(missing_ok=True)
        return True


template_file_service = TemplateFileService()
