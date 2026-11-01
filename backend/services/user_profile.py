"""Validate and normalize small, self-contained profile images."""
import base64
import binascii
from io import BytesIO

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_AVATAR_BYTES = 2 * 1024 * 1024


def normalize_avatar(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        header, encoded = value.split(',', 1)
        if header not in {'data:image/jpeg;base64', 'data:image/png;base64', 'data:image/webp;base64'}:
            raise ValueError('Unsupported image format')
        content = base64.b64decode(encoded, validate=True)
        if len(content) > MAX_AVATAR_BYTES:
            raise ValueError('Image exceeds 2 MB')
        with Image.open(BytesIO(content)) as source:
            if source.format not in {'JPEG', 'PNG', 'WEBP'} or source.width * source.height > 16_000_000:
                raise ValueError('Unsupported image format or dimensions')
            source.load()
            avatar = ImageOps.fit(ImageOps.exif_transpose(source).convert('RGBA'), (256, 256))
            output = BytesIO()
            avatar.save(output, format='WEBP', quality=85)
        return 'data:image/webp;base64,' + base64.b64encode(output.getvalue()).decode('ascii')
    except (ValueError, binascii.Error, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=422, detail='Use a valid PNG, JPEG or WebP image up to 2 MB and 16 megapixels') from exc
