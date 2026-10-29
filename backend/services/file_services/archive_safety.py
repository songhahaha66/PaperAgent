"""Bound compressed document parsing before XML/image/document libraries read it."""
import stat
import zipfile
from .workspace_layout import relative_parts


def validate_archive(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 2000:
            raise ValueError('压缩包文件数超过 2000')
        total = 0
        seen = set()
        for item in members:
            name = item.filename.rstrip('/')
            relative_parts(name)
            if name in seen or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                raise ValueError('压缩包含重复、加密或符号链接成员')
            seen.add(name)
            total += item.file_size
            if item.file_size > 32 * 1024 * 1024 or total > 128 * 1024 * 1024 or item.file_size > max(item.compress_size, 1) * 200:
                raise ValueError('压缩包解压资源超过限制')
