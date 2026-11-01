"""A completion receipt covers the code and its figures in one request."""
import asyncio
import pytest
from ai_system.sandbox import runtime_helpers as runtime


def test_cell_keeps_namespace_future_imports_and_last_expression(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(runtime,'setup',lambda root:None)
    monkeypatch.setattr(runtime,'save_figures',calls.append)
    ns={}
    result=runtime.run_cell('from __future__ import annotations\nx=41\ndef answer() -> Unknown:\n    return x+1\nanswer()', 'first', ns)
    assert result==42 and ns['answer'].__annotations__=={'return':'Unknown'}
    assert runtime.run_cell('x += 1\nx', 'second', ns)==42
    assert calls==['first','second']


@pytest.mark.parametrize('code,error',[('x=1\n1/0',ZeroDivisionError),('if :',SyntaxError)])
def test_cell_flushes_figures_even_when_code_fails(monkeypatch,code,error):
    calls=[]
    monkeypatch.setattr(runtime,'setup',lambda root:None)
    monkeypatch.setattr(runtime,'save_figures',calls.append)
    with pytest.raises(error):runtime.run_cell(code,'failed',{})
    assert calls==['failed']


def test_cell_saves_and_closes_real_figures_before_returning(tmp_path,monkeypatch):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    monkeypatch.setattr(runtime,'ROOT',tmp_path)
    monkeypatch.setattr(runtime,'setup',lambda root:None)
    runtime.run_cell('import matplotlib.pyplot as plt\nplt.plot([1,2],[3,4])\n17','figure',{})
    path=tmp_path/'outputs/figures/figure/figure_1.png'
    assert path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n') and not plt.get_fignums()


def test_adapter_submits_once_and_finalization_uses_the_process_receipt(tmp_path):
    from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
    from ai_system.sandbox.settings import SandboxSettings
    from ai_system.sandbox.contracts import ExecutionOutcome
    calls=[]
    async def submit(code,run_id,*,timeout):
        calls.append((code,run_id,timeout))
    async def poll(run_id,output):
        return ExecutionOutcome(exit_code=0,duration_seconds=.1)
    async def output(channel,text):pass
    async def run():
        session=OpenSandboxSession(SandboxSettings(api_key='fake',host_workspace_root='/cache'),workspace_dir=tmp_path)
        session.submit=submit
        session.poll=poll
        result=await session.execute('print(42)','one',output)
        await session.finalize('one')
        assert result.error is None and len(calls)==1
        assert calls==[('print(42)','one',180)] and session.context_id is None
    asyncio.run(run())
