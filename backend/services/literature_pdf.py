"""Isolated, resource-limited PDF text extraction. Input/output only; no network."""
import io
import json
import resource
import sys

resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
# Darwin does not support lowering address-space/data limits for this process.
# CPU time, input size and page/text budgets apply on every platform.
if sys.platform != 'darwin':
    memory_limit = resource.RLIMIT_AS
    hard_limit = resource.getrlimit(memory_limit)[1]
    cap = min(768 * 1024 * 1024, hard_limit) if hard_limit != resource.RLIM_INFINITY else 768 * 1024 * 1024
    resource.setrlimit(memory_limit, (cap, cap))
import pdfplumber

raw = sys.stdin.buffer.read(6_000_001)
if len(raw) > 6_000_000:
    raise ValueError('PDF exceeds budget')
rows = []
truncated = False
with pdfplumber.open(io.BytesIO(raw)) as pdf:
    title = str((pdf.metadata or {}).get('Title') or '')[:500]
    pages = len(pdf.pages)
    for number, page in enumerate(pdf.pages[:12], 1):
        extracted = page.extract_text() or ''
        truncated = truncated or len(extracted)>18000
        text = extracted[:18000]
        for paragraph, part in enumerate(text.split('\n\n'), 1):
            if part.strip():
                truncated = truncated or len(part.strip())>2000
                rows.append({'text':part.strip()[:2000], 'page':number,
                             'locator':f'第 {number} 页，提取段落 {paragraph}'})
            if len(rows) >= 40:
                break
        if len(rows) >= 40:
            break
print(json.dumps({'title':title, 'paragraphs':rows, 'truncated':truncated or pages>12 or len(rows)>=40}, ensure_ascii=False))
