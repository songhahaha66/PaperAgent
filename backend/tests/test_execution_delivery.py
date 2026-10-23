"""Research runtime contracts: reuse, provenance, transfer faults, and current status."""
import asyncio
import pytest

from helpers.sandbox import FakeSession
from helpers.research import make_store
from ai_system.sandbox.remote_kernel import RemoteKernel
from ai_system.sandbox.settings import SandboxSettings
from ai_system.sandbox.artifacts import capture_run_files
from ai_system.graph.context import GraphContext
from services.file_services.workspace_fs import WorkspaceFS


pytestmark = pytest.mark.usefixtures("offline_kernel")

@pytest.mark.parametrize('recovery', ['replacement', 'changed_file', 'lost_snapshot', 'different_cache'])
def test_rebuilt_kernel_recovers_only_original_outputs(tmp_path, recovery):
    class InterruptedFiles(FakeSession):
        offline = True
        async def download(self, path, destination, max_bytes):
            if self.offline:
                raise ConnectionError('download unavailable')
            await super().download(path, destination, max_bytes)
    async def run():
        session = InterruptedFiles(tmp_path)
        settings = SandboxSettings(provider='fake')
        kernel = RemoteKernel(tmp_path, session, settings)
        kernel.fs.write_text('outputs/table.csv', 'old local version')
        first = await kernel.execute('new remote version')
        assert first.ok and first.delivery_error
        assert kernel.fs.get_run_info(first.run_id)['output_manifest']
        await kernel.close()
        session.offline = False
        if recovery == 'changed_file':
            (session.remote / 'outputs/table.csv').write_text('later scratch bytes')
        elif recovery == 'lost_snapshot':
            import shutil
            shutil.rmtree(session.remote / '.pa-runs')
        elif recovery == 'different_cache':
            session.cache_key = 'f' * 64
        rebuilt = RemoteKernel(tmp_path, session, settings)
        try:
            if recovery in {'lost_snapshot', 'different_cache'}:
                with pytest.raises((RuntimeError, FileNotFoundError)):
                    await rebuilt.retry_outputs()
                assert rebuilt.pending_run == first.run_id
                assert (tmp_path / 'outputs/table.csv').read_text() == 'old local version'
            else:
                await rebuilt.retry_outputs()
                assert rebuilt.fs.get_run_info(first.run_id)['delivery_error'] is None
                assert (tmp_path / 'outputs/table.csv').read_text() == 'new remote version'
                assert rebuilt.fs.read_text(f'runs/{first.run_id}/artifacts/outputs/table.csv') == 'new remote version'
            assert session.calls == ['new remote version']
        finally:
            await rebuilt.close()
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['finalize', 'download'])
def test_cancelling_delivery_preserves_computation_for_restart(tmp_path, phase):
    async def run():
        entered = asyncio.Event()
        resume = False
        class Interrupted(FakeSession):
            async def finalize(self, run_id):
                if not resume and phase == 'finalize':
                    entered.set()
                    await asyncio.Event().wait()
            async def download(self, path, destination, max_bytes):
                if not resume and phase == 'download':
                    entered.set()
                    await asyncio.Event().wait()
                await super().download(path, destination, max_bytes)
        session = Interrupted(tmp_path)
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        task = asyncio.create_task(kernel.execute('42'))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        info = kernel.fs.get_run_info(kernel.pending_run)
        assert info['status'] == 'success' and info['error'] is None and info['delivery_error']
        resume = True
        if phase == 'download':
            await kernel.close()
        rebuilt = kernel if phase == 'finalize' else RemoteKernel(tmp_path, session, kernel.settings)
        await rebuilt.retry_outputs()
        assert (tmp_path/'outputs/table.csv').read_text() == '42' and session.calls == ['42']
        await rebuilt.close()
    asyncio.run(run())


@pytest.mark.parametrize('recovery', ['same', 'lost_context', 'replacement'])
def test_figure_finalization_retries_without_reexecuting_calculation(tmp_path, recovery):
    class Figures(FakeSession):
        offline = True
        async def finalize(self, run_id):
            if self.offline:
                raise RuntimeError('save figures failed')
            (self.remote / 'outputs/figure_1.png').write_bytes(b'figure')
    async def run():
        session = Figures(tmp_path)
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        first = await kernel.execute('42')
        assert first.ok and first.failure_kind is None and 'save figures failed' in first.delivery_error
        assert not (tmp_path / 'outputs/table.csv').exists(), 'Incomplete figures prevent freezing the output inventory'
        session.offline = False
        if recovery == 'lost_context':
            session.context_id = 'new-context'
        if recovery == 'replacement':
            await kernel.close()
        target = kernel if recovery != 'replacement' else RemoteKernel(tmp_path, session, kernel.settings)
        try:
            if recovery != 'same':
                with pytest.raises(RuntimeError, match='内存已失效'):
                    await target.retry_outputs()
                assert target.pending_run == first.run_id
            else:
                await target.retry_outputs()
                assert not target.fs.get_run_info(first.run_id)['delivery_error']
                assert target.fs.read_text(f'runs/{first.run_id}/artifacts/outputs/figure_1.png') == 'figure'
            assert session.calls == ['42']
        finally:
            await target.close()
            if target is not kernel:
                await kernel.close()
    asyncio.run(run())


def test_transfer_retry_never_reexecutes_or_invalidates_calculation(tmp_path):
    class OfflineFiles(FakeSession):
        offline = True
        async def download(self, path, destination, max_bytes):
            if self.offline: raise ConnectionError('file channel unavailable')
            await super().download(path, destination, max_bytes)
    async def run():
        session = OfflineFiles(tmp_path)
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        first = await kernel.execute('value\n42\n')
        assert first.ok and first.delivery_error
        assert kernel.fs.get_run_info(first.run_id)['status'] == 'success'
        blocked = await kernel.execute('must not run')
        assert blocked.failure_kind == 'delivery' and len(session.calls) == 1
        session.offline = False
        assert await kernel.retry_outputs() == first.run_id
        assert kernel.fs.get_run_info(first.run_id)['delivery_error'] is None
        assert len(session.calls) == 1
        assert (tmp_path / 'outputs/table.csv').read_text() == 'value\n42\n'
        await kernel.close()
    asyncio.run(run())


def test_root_csv_is_collected_and_private_metadata_is_rejected(tmp_path):
    from ai_system.sandbox.remote_kernel import safe_path
    class RootOutput(FakeSession):
        async def execute(self, code, run_id, output):
            result=await super().execute(code,run_id,output)
            (self.remote/'waiting_summary.csv').write_bytes(b'value\n42\n')
            return result
    async def run():
        kernel=RemoteKernel(tmp_path/'attempt',RootOutput(tmp_path/'attempt'),SandboxSettings(provider='fake'))
        result=await kernel.execute('value\n42\n')
        assert result.ok and any(p.endswith('/waiting_summary.csv') for p in result.files)
        public=WorkspaceFS(str(tmp_path/'public'));public.init_structure()
        files=capture_run_files(kernel.fs,public)
        assert any(p.endswith('/waiting_summary.csv') for p in files)
        assert public.abs(next(p for p in files if p.endswith('/waiting_summary.csv'))).read_text()=='value\n42\n'
        await kernel.close()
    asyncio.run(run())
    for path in ('manifest.json','chat_history.json','.system/state','outputs/../manifest.json'):
        with pytest.raises(ValueError):safe_path(path)


def test_completed_execution_event_already_links_published_logs(tmp_path, monkeypatch):
    from ai_system.modules.experiments.execution import build_tools
    from services.chat_services import conversation_timeline as timeline
    root = tmp_path/'public'
    ctx = GraphContext.create(str(tmp_path/'attempt'), planner_llm=None)
    ctx.research = make_store(root)
    ctx.delivery_fs = WorkspaceFS(str(root)); ctx.delivery_fs.init_structure()
    ctx.delivery_events = ctx.events
    events = []
    monkeypatch.setattr(timeline, 'execution_boundary',
        lambda root, rid, kind, item: events.append(timeline.execution_projection(root, rid, item)))
    async def run():
        try:
            tool = next(t for t in build_tools(ctx, 'Q1', record=False) if t.name == 'run_python')
            await tool.ainvoke({'code': 'print(sum([1, 2, 3]))'})
        finally:
            await ctx.kernels.close()
    asyncio.run(run())
    last = events[-1]
    assert last['status'] == 'completed'
    assert ctx.delivery_fs.abs(last['script_file']).is_file()
    assert ctx.delivery_fs.read_text(last['output_file']).strip() == '6'
