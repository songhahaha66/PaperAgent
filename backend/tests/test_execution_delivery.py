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


@pytest.mark.parametrize('failure,recovery', [
    ('inventory', 'same'), ('download', 'same'),
    ('inventory', 'replacement'), ('inventory', 'different_root'),
    ('download', 'replacement'), ('download', 'changed_file'),
])
def test_rebuilt_kernel_recovers_only_original_outputs(tmp_path, failure, recovery):
    class InterruptedFiles(FakeSession):
        offline = True
        async def files(self):
            if self.calls and self.offline and failure == 'inventory':
                raise ConnectionError('inventory unavailable')
            return await super().files()
        async def download(self, path, destination, max_bytes):
            if self.offline and failure == 'download':
                raise ConnectionError('download unavailable')
            await super().download(path, destination, max_bytes)
    async def run():
        session = InterruptedFiles()
        settings = SandboxSettings(provider='fake')
        kernel = RemoteKernel(tmp_path, session, settings)
        kernel.fs.write_text('outputs/table.csv', 'old local version')
        first = await kernel.execute('new remote version')
        assert first.ok and first.delivery_error
        info = kernel.fs.get_run_info(first.run_id)
        assert (info['pending_files'] is None) == (failure == 'inventory')
        session.offline = False
        if recovery == 'replacement':
            session.id = 'different-sandbox'
        elif recovery == 'different_root':
            session.root = '/workspace/another-experiment'
        elif recovery == 'changed_file':
            session.content.clear()
        rebuilt = RemoteKernel(tmp_path, session, settings)
        if recovery != 'same':
            with pytest.raises(RuntimeError, match='不可用'):
                await rebuilt.retry_outputs()
            assert rebuilt.fs.get_run_info(first.run_id)['delivery_error']
            assert rebuilt.pending_run == first.run_id
        else:
            await rebuilt.retry_outputs()
            assert rebuilt.fs.get_run_info(first.run_id)['delivery_error'] is None
            assert (tmp_path/'outputs/table.csv').read_text() == 'new remote version'
            assert rebuilt.fs.read_text(f'runs/{first.run_id}/artifacts/outputs/table.csv') == 'new remote version'
        assert session.calls == ['new remote version']
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
        session = Interrupted()
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        task = asyncio.create_task(kernel.execute('42'))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        info = kernel.fs.get_run_info(kernel.pending_run)
        assert info['status'] == 'success' and info['error'] is None and info['delivery_error']
        resume = True
        rebuilt = RemoteKernel(tmp_path, session, kernel.settings)
        await rebuilt.retry_outputs()
        assert (tmp_path/'outputs/table.csv').read_text() == '42' and session.calls == ['42']
        await rebuilt.close()
    asyncio.run(run())


@pytest.mark.parametrize('recovery', ['same', 'lost_context', 'lost_output'])
def test_figure_finalization_retries_without_reexecuting_calculation(tmp_path, recovery):
    class Figures(FakeSession):
        context_id = 'original-context'
        offline = True
        async def finalize(self, run_id):
            if self.offline:
                raise RuntimeError('save figures failed')
            self.content[f'runs/{run_id}/artifacts/figure_1.png'] = b'figure'
        async def download(self, path, destination, max_bytes):
            if self.offline and recovery == 'lost_output': raise ConnectionError('offline')
            await super().download(path, destination, max_bytes)
    async def run():
        session = Figures()
        kernel = RemoteKernel(tmp_path, session, SandboxSettings(provider='fake'))
        first = await kernel.execute('42')
        assert first.ok and first.error is None and first.failure_kind is None
        assert 'save figures failed' in first.delivery_error
        if recovery != 'lost_output': assert (tmp_path/'outputs/table.csv').read_text() == '42'
        assert kernel.fs.get_run_info(first.run_id)['status'] == 'success'
        session.offline = False
        if recovery == 'lost_context': session.context_id = 'new-context'
        if recovery == 'lost_output': session.content['outputs/table.csv'] = b'different version'
        rebuilt = RemoteKernel(tmp_path, session, kernel.settings)
        if recovery != 'same':
            with pytest.raises(RuntimeError, match='不可用'):
                await rebuilt.retry_outputs()
            assert rebuilt.fs.get_run_info(first.run_id)['delivery_error']
        else:
            await rebuilt.retry_outputs()
            info = rebuilt.fs.get_run_info(first.run_id)
            assert not info['finalize_pending'] and not info['delivery_error']
            assert rebuilt.fs.read_text(f'runs/{first.run_id}/artifacts/figure_1.png') == 'figure'
        assert session.calls == ['42']
        await rebuilt.close()
    asyncio.run(run())


def test_transfer_retry_never_reexecutes_or_invalidates_calculation(tmp_path):
    class OfflineFiles(FakeSession):
        offline = True
        async def download(self, path, destination, max_bytes):
            if self.offline: raise ConnectionError('file channel unavailable')
            await super().download(path, destination, max_bytes)
    async def run():
        session = OfflineFiles()
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
    import pytest
    class RootOutput(FakeSession):
        async def execute(self, code, run_id, output):
            result=await super().execute(code,run_id,output)
            self.content['waiting_summary.csv']=b'value\n42\n'
            return result
    async def run():
        kernel=RemoteKernel(tmp_path/'attempt',RootOutput(),SandboxSettings(provider='fake'))
        result=await kernel.execute('value\n42\n')
        assert result.ok and 'waiting_summary.csv' in result.files
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
