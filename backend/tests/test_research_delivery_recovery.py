"""Delivery recovery updates saved files/status without executing calculation again."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ai_system.graph.context import GraphContext
from ai_system.modules.registry import invoke
from ai_system.research.control import observe
from ai_system.research.loop import _set_action
from ai_system.research.runtime import ResearchRuntime
from ai_system.research.store import ResearchStore
from ai_system.sandbox.remote_kernel import RemoteKernel
from ai_system.sandbox.settings import SandboxSettings
from helpers.sandbox import FakeSession


class OfflineFiles(FakeSession):
    offline=True
    async def download(self,path,destination,max_bytes):
        if self.offline:raise ConnectionError('controlled file channel failure')
        await super().download(path,destination,max_bytes)


def context(tmp_path,session_type=FakeSession):
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    ctx.research=ResearchStore(tmp_path,'delivery');ctx.research.initialize('保存计算结果与文件')
    session=session_type(tmp_path)
    ctx.kernels._kernels['research']=RemoteKernel(tmp_path,session,SandboxSettings(provider='fake'),fs=ctx.fs)
    return ctx,session


async def compute(ctx,code='print(1)'):
    if not ctx.research.snapshot()['questions']:
        await invoke(ctx,'research.update_question',question='计算并保存结果')
    qid=next(iter(ctx.research.snapshot()['questions']))
    return await invoke(ctx,'research.run_python',question_id=qid,code=code)


@pytest.mark.parametrize('code,status', [('error','failed'),('disconnect','interrupted')])
def test_single_visible_operation_reports_actual_failure(tmp_path,code,status):
    ctx,session=context(tmp_path)
    async def run():
        try:
            result=await compute(ctx,code)
            assert result['execution_status']==('interrupted' if code=='disconnect' else 'failed')
            assert ctx.fs.get_run_info(result['run_id'])['status']=='error'
            state=ctx.research.snapshot()
            rows=[a for a in state['activity'] if a['capability'] in {'research.run_python','run_python'}]
            assert len(rows)==1
            assert rows[0]['status']==status and rows[0]['message']
            assert rows[0]['run_id']==result['run_id']
            assert not state['executions'] and not state['experiments']
            if code=='disconnect':
                again=await compute(ctx,code)
                assert again['recovered'] and len(ctx.fs._manifest['runs'])==1
            assert session.calls==[code]
        finally:await ctx.kernels.close()
    asyncio.run(run())


def test_later_code_publishes_and_indexes_original_pending_run(tmp_path):
    ctx,session=context(tmp_path,OfflineFiles)
    async def run():
        try:
            first=await compute(ctx)
            rid=first['run_id'];state=ctx.research.snapshot()
            assert first['execution_status']=='interrupted' and ctx.fs.get_run_info(rid)['delivery_error']
            assert next(a for a in state['activity'] if a['capability']=='research.run_python')['status']=='interrupted'
            session.offline=False
            await compute(ctx,'print(2)')
            path=f'outputs/research/{rid}/outputs/table.csv'
            assert ctx.fs.read_text(path)=='print(1)' and not ctx.fs.get_run_info(rid)['delivery_error']
            after=ctx.research.snapshot()
            assert not after['executions'] and not after['artifacts']
            assert ctx.fs.get_file_meta(path)['source_run']==rid
            assert session.calls==['print(1)','print(2)']
        finally:await ctx.kernels.close()
    asyncio.run(run())


@pytest.mark.parametrize('offline', [False,True])
def test_observe_recovers_files_before_writing_without_new_computation(tmp_path,offline):
    ctx,session=context(tmp_path,OfflineFiles)
    async def run():
        result=await compute(ctx)
        before=deepcopy(ctx.research.snapshot()['executions']);rid=result['run_id']
        await ctx.kernels.close()
        # Simulate restart with the confirmed cache, and no retained Python memory.
        resumed=GraphContext.create(str(tmp_path),planner_llm=None);resumed.research=ctx.research
        session.offline=offline
        resumed.kernels._kernels['research']=RemoteKernel(tmp_path,session,SandboxSettings(provider='fake'),fs=resumed.fs)
        with ctx.research._transaction() as db:
            state=ctx.research._read(db)
            state['publication']={'stage':'validate'}
            action=_set_action(state,'writing.validate',{},'整理已有结果')
            ctx.research._save(db,state,'test_pending_writing',{})
        budget=deepcopy(ResearchRuntime(tmp_path).status())
        if offline:
            with pytest.raises(ConnectionError,match='file channel'):
                await observe({},SimpleNamespace(context=resumed))
            assert ctx.research.snapshot()['action']==action
            assert not resumed.fs.exists(f'outputs/research/{rid}/outputs/table.csv')
        else:
            assert await observe({},SimpleNamespace(context=resumed))=={'research_blocked':False}
            path=f'outputs/research/{rid}/outputs/table.csv'
            assert resumed.fs.read_text(path)=='print(1)'
            assert resumed.fs.get_file_meta(path)['source_run']==rid
        assert not resumed.kernels._kernels, 'Compilation must be able to acquire the project lease'
        assert ctx.research.snapshot()['executions']==before and session.calls==['print(1)']
        assert ResearchRuntime(tmp_path).status()==budget
    asyncio.run(run())


def test_local_publication_failure_is_recovered_without_another_code_cell(tmp_path,monkeypatch):
    from ai_system.sandbox import artifacts
    from ai_system.modules.experiments.execution import recover_outputs
    ctx,session=context(tmp_path)
    capture=artifacts.capture_run_files
    def unavailable(*args,**kwargs):raise OSError('controlled publication failure')
    monkeypatch.setattr(artifacts,'capture_run_files',unavailable)
    async def run():
        try:
            result=await compute(ctx)
            state=ctx.research.snapshot();rid=result['run_id']
            assert next(a for a in state['activity'] if a['capability']=='research.run_python')['status']=='interrupted'
            assert not ctx.fs.get_run_info(rid)['delivery_error'], 'Download itself succeeded'
            monkeypatch.setattr(artifacts,'capture_run_files',capture)
            await recover_outputs(ctx)
            assert ctx.fs.read_text(f'outputs/research/{rid}/outputs/table.csv')=='print(1)'
            assert session.calls==['print(1)'] and not ctx.research.snapshot()['executions']
            stable=deepcopy(ctx.research.snapshot())
            await recover_outputs(ctx)
            assert ctx.research.snapshot()==stable, 'Recovery is idempotent'
        finally:await ctx.kernels.close()
    asyncio.run(run())


def test_missing_published_file_is_restored_from_receipt_without_recomputing(tmp_path):
    from ai_system.modules.experiments.execution import recover_outputs
    ctx,session=context(tmp_path)
    async def run():
        try:
            result=await compute(ctx);rid=result['run_id']
            path=f'outputs/research/{rid}/outputs/table.csv'
            ctx.fs.abs(path).unlink()
            await recover_outputs(ctx)
            assert ctx.fs.read_text(path)=='print(1)'
            assert session.calls==['print(1)'] and not ctx.research.snapshot()['experiments']
        finally:await ctx.kernels.close()
    asyncio.run(run())
