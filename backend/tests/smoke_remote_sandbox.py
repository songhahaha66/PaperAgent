"""Explicit integration check; never collected by pytest or executed by normal tests.

PAPERAGENT_SANDBOX_CONFIG=.sandbox.env uv run --extra sandbox python tests/smoke_remote_sandbox.py
"""

if __name__ == '__main__':
    raise SystemExit('此脚本针对已移除的文件镜像协议；v2 请按 deploy/sandbox/README.md 用户实测清单验证，禁止误连旧远端沙箱。')

import asyncio
import json
import tempfile
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.sandbox.factory import create_kernel
from ai_system.sandbox.settings import SandboxSettings


async def main():
    settings = SandboxSettings.from_env()
    assert settings.provider != 'local', 'Explicit remote configuration required'
    with tempfile.TemporaryDirectory(prefix='paperagent-remote-smoke-') as directory:
        kernel = create_kernel(directory, settings=settings)
        started = time.monotonic()
        events = []
        async def event(value):
            events.append(value)
            if value['type'] in {'output', 'finished'}:
                print(json.dumps({'seconds': round(time.monotonic()-started, 1), **value}, ensure_ascii=False), flush=True)
        try:
            first = await kernel.execute("import os,sys,subprocess,time\nx=21\nprint('python stdout',flush=True)\nprint('python stderr',file=sys.stderr,flush=True)\nos.write(1,b'fd stdout\\n')\nsubprocess.run([sys.executable,'-c',\"print('child stdout')\"])\ntime.sleep(1)\nprint('after sleep',flush=True)", on_event=event)
            assert first.ok, first.error
            assert all(s in first.stdout for s in ('python stdout','python stderr','fd stdout','child stdout'))
            second = await kernel.execute("import pandas as pd\nimport matplotlib.pyplot as plt\nprint(x*2)\npd.DataFrame({'value':[x, x*2]}).to_csv('outputs/result.csv',index=False)\nplt.plot([1,2],[x,x*2])\nplt.show()", on_event=event)
            assert second.ok, second.error
            assert '42' in second.stdout
            assert second.figures and 'outputs/result.csv' in second.files
            # Files introduced after startup must be uploaded before the next cell.
            data = Path(directory) / 'data/next.csv'
            data.parent.mkdir(exist_ok=True)
            data.write_text('value\n7\n')
            followup = await kernel.execute("print(pd.read_csv('data/next.csv').iloc[0,0])")
            assert followup.ok and '7' in followup.stdout, followup.error
            # Enforced deny includes direct-IP traffic, not only failed DNS.
            network = await kernel.execute("import socket\ns=socket.socket();s.settimeout(0.5)\ntry:\n s.connect(('1.1.1.1',443)); print('NETWORK_OPEN')\nexcept OSError:\n print('NETWORK_BLOCKED')\nfinally:\n s.close()")
            assert network.ok and 'NETWORK_BLOCKED' in network.stdout, network.error
            kernel.timeout = 1
            timed = await kernel.execute("from pathlib import Path\nPath('outputs/partial.csv').write_text('value\\n9\\n')\nprint('before timeout',flush=True)\nimport time\ntime.sleep(30)", on_event=event)
            assert timed.timed_out and 'before timeout' in timed.stdout
            assert kernel.fs.get_run_info(timed.run_id)['status'] == 'timeout'
            assert (Path(directory) / 'outputs/partial.csv').exists()
            kernel.timeout = 180
            restarted = await kernel.execute("print('x' in globals())")
            assert restarted.ok and 'False' in restarted.stdout, restarted.error
            received = asyncio.Event()
            async def cancel_event(value):
                await event(value)
                if value['type'] == 'output':
                    received.set()
            task = asyncio.create_task(kernel.execute("print('before cancel',flush=True)\nimport time\ntime.sleep(30)", on_event=cancel_event))
            await asyncio.wait_for(received.wait(), 20)
            task.cancel()
            try:
                await task
                raise AssertionError('Cancellation must propagate')
            except asyncio.CancelledError:
                pass
            assert events[-1]['status'] == 'cancelled'
            await kernel.close()
            assert (Path(directory) / 'outputs/result.csv').read_text().strip() == 'value\n21\n42'
            assert (Path(directory) / second.figures[0]).stat().st_size > 0
            print('PASS: remote state, streaming, native/child output, file sync, CSV/PNG, network deny, timeout/cancel, restart and local persistence after destroy', flush=True)
        finally:
            await kernel.close()


if __name__ == '__main__':
    asyncio.run(main())
