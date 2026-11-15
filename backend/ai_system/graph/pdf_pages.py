"""Shared, version-bound PDF pages for source material and paper previews."""
import io
import threading
from contextlib import closing

from services.file_services.workspace_layout import workspace_path

PDF_LOCK = threading.Lock()  # PDFium's native API is not thread-safe.


def render_pdf_pages(root, pdf, pdf_hash, pages):
    import pypdfium2 as pdfium
    from ai_system.agent.images import page_ref
    from services.file_services.file_helper import atomic_write
    images = []
    texts = []
    with PDF_LOCK:
        with pdfium.PdfDocument(pdf) as document:
            if any(p < 1 or p > len(document) for p in pages):
                raise ValueError('PDF页码超出范围，总页数：' + str(len(document)))
            for number in dict.fromkeys(pages):
                name = f'.system/paper-project/previews/{pdf_hash}/{number}.png'
                target = workspace_path(root, name)
                with closing(document[number - 1]) as page:
                    with closing(page.get_textpage()) as textpage:
                        texts.append({'page': number, 'text': textpage.get_text_range()[:12000]})
                    if not target.exists():
                        width, height = page.get_size()
                        if width <= 0 or height <= 0:
                            raise ValueError('PDF页面尺寸无效')
                        bitmap = page.render(scale=min(1.5, 1600 / max(width, height)))
                        try:
                            buffer = io.BytesIO()
                            bitmap.to_pil().save(buffer, format='PNG')
                        finally:
                            bitmap.close()
                        atomic_write(target, lambda stream: stream.write(buffer.getvalue()))
                images.append(page_ref(root, {'page': number, 'path': name}))
            total = len(document)
    return {'pdf_sha256': pdf_hash, 'total_pages': total, 'pages': texts, '_images': images}
