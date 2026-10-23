"""Explicit real-server acceptance. Never collected by pytest.

Run with --config pointing to a private env file and --output to a new directory.
Uses isolated backend state and trusted fixtures, without model/database calls.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys


async def check(output):
    from ai_system.sandbox.factory import create_kernel
    from ai_system.sandbox.settings import SandboxSettings
    from ai_system.export.latex import compile_pdf
    from ai_system.export.docx import export_docx

    settings = SandboxSettings.from_env()
    root = output / 'project-files' / 'acceptance'
    kernel = create_kernel(root, settings=settings)
    report = {'started_at': datetime.now(timezone.utc).isoformat(),
              'provider': settings.provider, 'image': settings.image,
              'cpu': settings.cpu, 'memory': settings.memory, 'checks': []}

    def passed(name):
        report['checks'].append(name)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print('PASS:', name, flush=True)

    def success(result):
        if not result.ok or result.delivery_error:
            raise RuntimeError(result.error or result.delivery_error)

    try:
        kernel.fs.write_text('data/input.csv', 'value\n1\n2\n3\n')
        first = await kernel.execute("import pandas as pd\nx=21\nprint('TOTAL', pd.read_csv('data/input.csv')['value'].sum())")
        success(first)
        assert 'TOTAL 6' in first.stdout, 'Uploaded CSV did not produce the expected total'
        passed('Python execution and uploaded CSV')
        second = await kernel.execute("import matplotlib.pyplot as plt\nprint('STATE', x*2)\npd.DataFrame({'value':[x,x*2]}).to_csv('outputs/result.csv',index=False)\nplt.plot([1,2],[x,x*2])\nplt.show()")
        success(second)
        assert 'STATE 42' in second.stdout and second.figures, 'Python state or figure delivery missing'
        assert (root / 'outputs/result.csv').read_text().strip() == 'value\n21\n42'
        assert all(kernel.fs.abs(path).is_file() for path in second.figures)
        passed('Persistent Python state, CSV and figure downloads')
        kernel.fs.write_text('data/input.csv', 'value\n7\n8\n')
        changed = await kernel.execute("print('UPDATED', pd.read_csv('data/input.csv')['value'].sum())")
        success(changed)
        assert 'UPDATED 15' in changed.stdout, 'Updated CSV did not reach the sandbox'
        passed('Incremental input update')
        denied = await kernel.execute("import socket\ns=socket.socket();s.settimeout(2)\ntry:\n s.connect(('1.1.1.1',443)); print('NETWORK_OPEN')\nexcept OSError:\n print('NETWORK_BLOCKED')\nfinally:\n s.close()")
        success(denied)
        assert 'NETWORK_BLOCKED' in denied.stdout and 'NETWORK_OPEN' not in denied.stdout, 'External TCP access was not blocked'
        passed('External TCP connection blocked')
        report['sandbox_id'] = kernel.session.id
        report['cache_key'] = kernel.session.cache_key
    finally:
        await kernel.close()
    passed('Research sandbox destroyed; downloaded files retained')

    (root / 'paper.tex').write_text(
        '\\documentclass[UTF8,fontset=fandol]{ctexart}\n'
        '\\begin{document}\n沙箱验收：$1+1=2$。\\end{document}\n')
    pdf = await compile_pdf(root)
    assert pdf.success, pdf.error
    assert (root / 'paper.pdf').read_bytes().startswith(b'%PDF')
    import pdfplumber
    with pdfplumber.open(root / 'paper.pdf') as document:
        assert '沙箱验收' in ''.join(page.extract_text() or '' for page in document.pages)
    passed('Chinese XeLaTeX PDF compilation and download')
    docx = await export_docx(root, '# 沙箱验收\n\nPython、文件传输和论文编译。\n')
    assert Path(docx.docx_path).exists() or (root / docx.docx_path).exists()
    assert (root / 'paper.docx').read_bytes().startswith(b'PK')
    from docx import Document
    assert 'Python、文件传输和论文编译。' in '\n'.join(p.text for p in Document(root / 'paper.docx').paragraphs)
    passed('Pandoc Word export with reference roundtrip')
    print('Acceptance output:', output, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = args.config.resolve(strict=True)
    # macOS /tmp is a symlink; use one canonical root for kernel and compiler.
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    # Set before importing config.paths or other backend modules.
    os.environ['PAPERAGENT_SANDBOX_CONFIG'] = str(config)
    os.environ['PA_DATA_PATH'] = str(output)
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ai_system.sandbox.settings import SandboxSettings
    settings = SandboxSettings.from_env()
    try:
        asyncio.run(check(output))
    except Exception as exc:
        message = str(exc)
        if settings.api_key:
            message = message.replace(settings.api_key, '[redacted]')
        raise SystemExit('Acceptance failed: ' + message) from None


if __name__ == '__main__':
    main()
