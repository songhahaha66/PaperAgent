"""Explicit remote lease lifecycle acceptance, no model calls."""

if __name__ == '__main__':
    raise SystemExit('此脚本针对已移除的文件镜像协议；v2 请按 deploy/sandbox/README.md 用户实测清单验证，禁止误连旧远端沙箱。')

import asyncio
import tempfile
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.sandbox.factory import create_kernel
from ai_system.sandbox.settings import SandboxSettings
from ai_system.sandbox.opensandbox_provider import _WORK_SANDBOXES, OpenSandboxSession

async def main():
    settings=SandboxSettings.from_env()
    assert settings.provider=='opensandbox'
    with tempfile.TemporaryDirectory() as directory:
        def kernel(name, experiment, work='work-one'):
            return create_kernel(Path(directory)/name,settings=settings,work_id=directory+work,experiment_id=experiment)
        first=kernel('first','E1')
        second=kernel('second','E2')
        continued=kernel('continued','E1')
        parallel=kernel('parallel','E3')
        other=kernel('other','E1','work-two')
        try:
            result=await first.execute("value=42\nfrom pathlib import Path\nPath('one.csv').write_text('value\\n42')\nprint('answer',value)")
            assert result.ok,result.error
            assert 'one.csv' in result.files, result.files
            identity=first.session.id
            await first.close()
            result=await second.execute("print('value' in globals())\nfrom pathlib import Path\nprint(Path('one.csv').exists())")
            assert result.ok and result.stdout.count('False')==2,result.stdout
            assert second.session.id==identity
            result=await parallel.execute("print('value' in globals())")
            assert result.ok and 'False' in result.stdout
            assert parallel.session.id!=identity
            await parallel.close()
            await second.close()
            result=await continued.execute("print(value)\nfrom pathlib import Path\nprint(Path('one.csv').read_text())")
            assert result.ok and '42' in result.stdout,result.error
            assert continued.session.id==identity and continued.session.context_restored
            result=await other.execute("print('value' in globals())")
            assert result.ok and 'False' in result.stdout
            assert other.session.id!=identity
            print('PASS: same-work container reuse; independent namespaces/variables; same-experiment memory; parallel and cross-work isolation',flush=True)
        finally:
            for k in (first,second,continued,parallel,other): await k.close()
            for key,host in list(_WORK_SANDBOXES.items()):
                if key[0].startswith(directory):
                    _WORK_SANDBOXES.pop(key,None)
                    await OpenSandboxSession._destroy_host(host)

asyncio.run(main())
