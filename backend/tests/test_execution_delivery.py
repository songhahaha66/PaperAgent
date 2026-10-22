"""Research runtime contracts: reuse, provenance, transfer faults, and current status."""
import asyncio
import json
from types import SimpleNamespace

from test_sandbox_providers import FakeSession
from test_research_control import make_store, finish
from ai_system.sandbox.remote_kernel import RemoteKernel
from ai_system.sandbox.settings import SandboxSettings
from ai_system.sandbox.kernel import PythonKernel
from ai_system.sandbox.artifacts import capture_run_files, collect_references
from ai_system.graph.context import GraphContext
from ai_system.research.views import delivery_summary
from services.file_services.workspace_fs import WorkspaceFS


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


def test_continuation_publishes_each_run_only_once(tmp_path):
    async def run():
        kernel = RemoteKernel(tmp_path/'attempt', FakeSession(), SandboxSettings(provider='fake'))
        cell = await kernel.execute('value\n42\n')
        root = WorkspaceFS(str(tmp_path/'root')); root.init_structure()
        first = capture_run_files(kernel.fs, root, 'attempt-1')
        assert first
        assert capture_run_files(kernel.fs, root, 'attempt-2') == []
        assert len(root.list_visible_files()) == len(first)
        assert all(root.get_file_meta(p)['source_run'] == cell.run_id for p in first)
        await kernel.close()
    asyncio.run(run())


def test_explicit_file_scope_invalidates_only_consumers_and_dependencies(tmp_path):
    store = make_store(tmp_path)
    data = tmp_path/'data'; data.mkdir()
    (data/'one.csv').write_text('value\n1')
    (data/'two.csv').write_text('value\n2')
    with store._transaction() as db:
        state=store._read(db)
        state['experiments']['Q1']['input_files']=['data/one.csv']
        state['experiments']['Q2']['input_files']=[]
        state['experiments']['Q3']['input_files']=['data/two.csv']
        store._save(db,state,'test_scope',{})
    for key in ('Q1','Q2','Q3'): finish(store,key)
    (data/'one.csv').write_text('value\n9')
    state=store.refresh_inputs()
    assert state['experiments']['Q1']['validity'] == 'stale'
    assert state['experiments']['Q2']['validity'] == 'stale'
    assert state['experiments']['Q3']['validity'] == 'current'


def test_structured_output_attaches_actual_run_and_deduplicates_figures(tmp_path):
    async def run():
        ctx = GraphContext.create(str(tmp_path), planner_llm=None)
        kernel = PythonKernel(str(tmp_path), fs=ctx.fs)
        try:
            result = await kernel.execute("emit_result('mean', 7.0, unit='分钟')\nimport matplotlib.pyplot as plt\nplt.plot([1,2],[3,4])")
            assert result.ok, result.error
            refs = collect_references(ctx, 'E1', result)
            assert len(refs) == 2
            assert refs[0].value == '7.0' and refs[0].run_id == result.run_id
            figure = ctx.registry.record_figure('E1', result.run_id, result.figures[0], '中文图题')
            assert figure.id == refs[1].id
            assert len(ctx.registry.results) == 2
        finally: await kernel.close()
    asyncio.run(run())


def test_local_stdout_arrives_before_completion_and_csv_is_captured(tmp_path):
    async def run():
        kernel = PythonKernel(str(tmp_path))
        live = asyncio.Event()
        events=[]
        async def event(row):
            events.append(row)
            if row['type']=='output' and 'first' in row['text']: live.set()
        task=asyncio.create_task(kernel.execute("import time\nprint('first',flush=True)\ntime.sleep(0.5)\nfrom pathlib import Path\nPath('outputs/data.csv').write_text('value\\n7')\nprint('last')",on_event=event))
        await asyncio.wait_for(live.wait(),20)
        assert not task.done()
        result=await task
        assert result.ok and 'outputs/data.csv' in result.files
        assert kernel.fs.abs(f'runs/{result.run_id}/artifacts/outputs/data.csv').exists()
        assert events[-1]['type']=='finished'
        await kernel.close()
    asyncio.run(run())


def test_local_close_cancellation_still_kills_child(tmp_path):
    import pytest
    class Process:
        returncode = None
        stdin = SimpleNamespace(close=lambda: None)
        def kill(self): self.returncode = -9
        async def wait(self):
            if self.returncode is None: raise asyncio.CancelledError()
            return self.returncode
    async def run():
        kernel = PythonKernel(str(tmp_path))
        process = kernel._proc = Process()
        with pytest.raises(asyncio.CancelledError): await kernel.close()
        assert process.returncode == -9 and kernel._proc is None
    asyncio.run(run())


def test_report_export_failure_does_not_claim_research_still_running():
    state = {'delivery_stages': {'write':{'status':'completed'}, 'render':{'status':'completed'}, 'export':{'status':'blocked'}}}
    summary = delivery_summary(state)
    assert summary['state']=='incomplete' and summary['stage']=='export'
    assert '已有计算和文件已保留' in summary['label']


def test_paused_work_cannot_display_report_still_running(tmp_path):
    from ai_system.research.control import public_snapshot
    state = make_store(tmp_path).snapshot()
    state['delivery_stages'] = {'write': {'status': 'in_progress'}}
    public = public_snapshot(state, work_status='paused')
    assert public['delivery_stages']['write']['status'] == 'interrupted'
    assert public['delivery_summary']['state'] == 'incomplete'


def test_unused_historical_figure_does_not_force_recomputation(tmp_path):
    from ai_system.graph.registry import Result
    from ai_system.modules.experiments.review import report_submission_issues
    from test_report_submission import branch, seed, report
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    runs=[]
    good=seed(ctx,runs)
    ctx.registry.results['F99']=Result('F99','figure','Q1','过期图','','old-run',path='outputs/missing.png')
    assert not report_submission_issues(ctx,branch(),report(good.id),runs)
    payload=report(good.id)
    payload['summary']+=' {{F:F99}}'
    assert report_submission_issues(ctx,branch(),payload,runs)


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


def test_exported_code_includes_structured_output_helper(tmp_path, capsys, monkeypatch):
    from ai_system.modules.experiments.execution import export_solution_code
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    export_solution_code(ctx,'E1',[{'run_id':'original','ok':True,'code':"emit_result('answer',sum([1,2,3]))"}])
    code=ctx.fs.read_text('code/E1_solution.py')
    monkeypatch.chdir(tmp_path)
    exec(compile(code,'E1_solution.py','exec'),{})
    assert '"value": 6' in capsys.readouterr().out


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
