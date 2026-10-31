"""Architecture regressions on separate filesystems; no external service or model."""
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import pytest
from helpers.remote_files import RemoteFiles
from ai_system.sandbox.contracts import ExecutionOutcome
from ai_system.sandbox.remote_kernel import RemoteKernel
from ai_system.sandbox.settings import SandboxSettings
from ai_system.sandbox.content_store import ContentStore
from services.chat_services import paper_project as project
from services.file_services.workspace_layout import workspace_path


class Session(RemoteFiles):
    async def execute(self,code,run_id,output):
        self.calls.append(code)
        if code=='create data':(self.remote/'data/generated.npz').write_bytes(b'versioned computation data'*100)
        if code=='x = 41':self.x=41
        if code=='print(x)':await output('stdout',str(self.x))
        return ExecutionOutcome()
    async def interrupt(self):pass


def context(root):
    from ai_system.graph.context import GraphContext
    from ai_system.research.store import ResearchStore
    ctx=GraphContext.create(str(root),planner_llm=None)
    ctx.research=ResearchStore(root,'architecture');ctx.research.initialize('用原始资料计算并保存正文。')
    return ctx


def test_hundred_cells_reuse_generated_input_bytes(tmp_path):
    async def run():
        root=tmp_path/'project';session=Session(root)
        kernel=RemoteKernel(root,session,SandboxSettings(provider='fake'))
        first=await kernel.execute('create data')
        assert first.ok and not first.delivery_error
        for _ in range(100):
            result=await kernel.execute('read data')
            assert result.ok and not result.delivery_error
        snapshots=[kernel.fs.run_dir(rid)/'inputs/data/generated.npz' for rid in list(kernel.fs._manifest['runs'])[1:]]
        assert len({p.stat().st_ino for p in snapshots})==1
        assert len([p for p in session.downloads if p.endswith('/data/generated.npz')])==1
        assert not (root/'data/generated.npz').exists(), 'derived data must not replace original user inputs'
        from ai_system.sandbox.artifacts import inherit_execution_files
        from services.file_services.workspace_fs import WorkspaceFS
        target=WorkspaceFS(tmp_path/'child');target.init_structure()
        target.write_text('data/generated.npz','original child material')
        selected=list(kernel.fs._manifest['runs'])[:2]
        assert inherit_execution_files(root,target,'research',run_ids=selected)==set(selected)
        assert target.read_text('data/generated.npz')=='original child material'
        assert target.abs('outputs/intermediate/imported/'+first.run_id+'/data/generated.npz').read_bytes()==b'versioned computation data'*100
        assert target.abs('runs/'+selected[0]+'/artifacts/data/generated.npz').stat().st_ino==target.abs('runs/'+selected[1]+'/inputs/data/generated.npz').stat().st_ino
        await kernel.close()
    asyncio.run(run())


def test_preparation_timeout_preserves_runtime_and_memory(tmp_path):
    async def run():
        session=Session(tmp_path/'project');kernel=RemoteKernel(session.backend_root,session,SandboxSettings(provider='fake'))
        assert (await kernel.execute('x = 41')).ok
        identity=session.id;original=kernel.transfer.freeze_actual_inputs
        async def timeout(_):raise TimeoutError('transfer timed out')
        kernel.transfer.freeze_actual_inputs=timeout
        failed=await kernel.execute('must not submit')
        info=kernel.fs.get_run_info(failed.run_id)
        assert info['submission_state']=='not_submitted' and info['failure_stage']=='snapshot_remote_inputs'
        assert session.calls==['x = 41'] and session.id==identity and not session.closed
        kernel.transfer.freeze_actual_inputs=original
        recovered=await kernel.execute('print(x)')
        assert recovered.ok and recovered.stdout=='41' and session.id==identity
        await kernel.close()
    asyncio.run(run())


def test_writing_and_disconnect_keep_live_python_context(tmp_path):
    from ai_system.modules.registry import invoke
    async def run():
        ctx=context(tmp_path);session=Session(tmp_path)
        kernel=RemoteKernel(tmp_path,session,SandboxSettings(provider='fake'),fs=ctx.fs)
        ctx.kernels._kernels['research']=kernel
        assert (await kernel.execute('x = 41')).ok
        identity,context_id=session.id,session.context_id
        await invoke(ctx,'writing.edit',path='paper.md',content='正文不应丢失计算变量。')
        assert (await kernel.execute('print(x)')).stdout=='41'
        await kernel.disconnect()
        following=Session(tmp_path);following.x=session.x
        resumed=RemoteKernel(tmp_path,following,SandboxSettings(provider='fake'),fs=ctx.fs)
        result=await resumed.execute('print(x)')
        assert result.stdout=='41' and following.context_restored
        assert following.id==identity and following.context_id==context_id
        assert kernel.fs.get_run_info(result.run_id)['runtime_epoch']==1
        await resumed.close()
    asyncio.run(run())


def test_missing_image_cannot_hide_behind_placeholder(tmp_path):
    from ai_system.export.latex import source_diagnostics
    (tmp_path/'paper.tex').write_text(r'\begin{document}\IfFileExists{figures/missing.png}{\includegraphics{figures/missing.png}}{\fbox{figure}}\end{document}')
    issues=source_diagnostics(tmp_path,'paper.tex')
    assert any('figures/missing.png' in row['message'] for row in issues)


def test_asset_is_versioned_and_never_overwrites_a_previous_selection(tmp_path):
    from ai_system.modules.registry import invoke
    ctx=context(tmp_path)
    ctx.fs.write_text('outputs/example.png','first figure');ctx.fs.register_file('outputs/example.png',kind='data',visibility='user')
    (tmp_path/'paper.tex').write_text(r'\begin{document}\includegraphics{figures/chart.png}\end{document}')
    async def run():
        await invoke(ctx,'writing.use_asset',source='outputs/example.png',destination='figures/chart.png')
        first=project.snapshot(tmp_path)
        ctx.fs.write_text('outputs/example.png','new calculation')
        assert project.snapshot(tmp_path)['version']==first['version']
        with pytest.raises(ValueError,match='已有不同内容'):
            await invoke(ctx,'writing.use_asset',source='outputs/example.png',destination='figures/chart.png')
        archived=workspace_path(tmp_path,project.VERSIONS)/first['version']/'figures/chart.png'
        assert archived.read_text()=='first figure'
        (tmp_path/'figures/chart.png').write_text('manual edit')
        assert archived.read_text()=='first figure' and project.snapshot(tmp_path)['version']!=first['version']
    asyncio.run(run())


def test_actual_pdf_pages_and_model_images_are_version_bound(tmp_path):
    import pypdfium2 as pdfium
    from ai_system.modules.writing.document import render_pages,tool_content
    pdf=tmp_path/'example.pdf'
    with pdfium.PdfDocument.new() as doc:
        doc.new_page(600,800);doc.new_page(600,800);doc.save(pdf)
    content=ContentStore(tmp_path,20_000_000);pdf_hash=content.put(pdf);version='a'*64
    archive=workspace_path(tmp_path,project.VERSIONS)/version
    project.atomic_text(archive/'builds'/f'{pdf_hash}.json',json.dumps({'source_version':version,'pdf_sha256':pdf_hash,'warnings':['Overfull box']}))
    result=render_pages(tmp_path,version,pdf_hash,[2])
    assert result['total_pages']==2 and result['_images'][0]['page']==2
    ctx=SimpleNamespace(workspace=tmp_path)
    from ai_system.graph.images import ImageProjection
    from langchain_core.messages import ToolMessage
    receipt=ToolMessage(content=tool_content(ctx,result,None),tool_call_id='preview')
    assert 'base64' not in receipt.content
    media=ImageProjection(tmp_path,SimpleNamespace(profile={'image_inputs':True,'image_tokens_per_image':4096}),{})
    image=media.hydrate(media.view([receipt],max_tokens=10000))[0].content
    assert image[1]['image_url']['url'].startswith('data:image/png;base64,')
    text=ImageProjection(tmp_path,SimpleNamespace(profile={}),{}).view([receipt],max_tokens=10000)[0]
    assert '不能声称已做视觉检查' in text.content
    with pytest.raises(ValueError,match='超出范围'):render_pages(tmp_path,version,pdf_hash,[3])
    with pytest.raises(FileNotFoundError):render_pages(tmp_path,'b'*64,pdf_hash,[1])


def test_snapshot_push_coalesces_and_stops_after_close():
    from services.chat_services.research_push import ResearchPush
    async def run():
        sent=[];reads=[]
        def snapshot():reads.append(1);return SimpleNamespace(model_dump=lambda:{'commit_seq':7})
        async def publish(kind,value):sent.append((kind,value))
        push=ResearchPush(snapshot,publish,delay=.001)
        for _ in range(50):push.schedule()
        await push.task
        assert reads==[1] and sent==[('research_snapshot',{'commit_seq':7})]
        await push.close();push.schedule();assert len(sent)==1
    asyncio.run(run())


def test_review_task_reads_actual_pdf_without_writer_or_kernel(tmp_path,monkeypatch):
    import pypdfium2 as pdfium
    from langchain_core.messages import AIMessage,ToolMessage
    from research_tools_helpers import ResearchToolsModel,tool
    from ai_system.modules.registry import invoke
    def child(messages):
        receipts=[m for m in messages if isinstance(m,ToolMessage)]
        if not receipts:return tool('view_pdf_pages',{'pages':[1]},'review-page')
        if len(receipts)==1:
            assert isinstance(receipts[0].content,list)
            assert receipts[0].content[1]['type']=='image_url'
            return tool('read_file',{'path':'paper.tex'},'review-source')
        return AIMessage(content='第1页已看；仅复核提供的版本，未执行计算。')
    model=ResearchToolsModel(respond=child,profile={'image_inputs':True,'max_input_tokens':128000},
        metadata={'generation_settings':{'image_tokens_per_image':4096}})
    ctx=context(tmp_path);ctx.coder_llm=model
    (tmp_path/'paper.tex').write_text(r'\begin{document}Known manuscript\end{document}')
    version=project.snapshot(tmp_path)['version'];pdf=tmp_path/'paper.pdf'
    with pdfium.PdfDocument.new() as doc:doc.new_page(600,800);doc.save(pdf)
    content=ContentStore(tmp_path,20_000_000);pdf_hash=content.put(pdf)
    archive=workspace_path(tmp_path,project.VERSIONS)/version
    project.atomic_text(archive/'builds'/f'{pdf_hash}.json',json.dumps({'source_version':version,'pdf_sha256':pdf_hash}))
    def forbidden(*args,**kwargs):raise AssertionError('review must not access an execution kernel')
    monkeypatch.setattr(ctx.kernels,'get',forbidden)
    async def run():
        value=await invoke(ctx,'research.delegate',purpose='document_review',task='逐页检查实际稿件',
            source_version=version,pdf_sha256=pdf_hash)
        assert value['status']=='completed' and value['advisory'] and value['reviewed_pages']==[1]
        assert b'data:image/png;base64' not in workspace_path(tmp_path,'.system/graph.sqlite').read_bytes()
        assert (tmp_path/'paper.tex').read_text()==r'\begin{document}Known manuscript\end{document}'
        calls=model.calls
        cached=await invoke(ctx,'research.delegate',purpose='document_review',task_id=value['task_id'],
            task='逐页检查实际稿件',source_version=version,pdf_sha256=pdf_hash)
        assert cached['answer']==value['answer'] and model.calls==calls
        (tmp_path/'paper.tex').write_text('changed')
        stale=await invoke(ctx,'research.delegate',purpose='document_review',task_id=value['task_id'],
            task='逐页检查实际稿件',source_version=version,pdf_sha256=pdf_hash)
        assert not stale['current'] and model.calls==calls
    asyncio.run(run())


def test_clarification_has_one_idempotent_writer(tmp_path):
    from ai_system.research.clarifications import receive
    from ai_system.research.models import ResearchConflict
    ctx=context(tmp_path);store=ctx.research
    with store._transaction() as db:
        state=store._read(db)
        state['action']={'id':'question-1','capability':'ask','status':'waiting','inputs':{'message':'价格单位？'}}
        store._save(db,state,'fixture',{})
    first=receive(store,'question-1','元/kWh')
    clock=store.snapshot()['commit_seq']
    assert receive(store,'question-1','元/kWh')==first
    assert len(store.snapshot()['clarifications'])==1 and store.snapshot()['commit_seq']==clock
    with pytest.raises(ResearchConflict):receive(store,'question-1','元/MWh')
