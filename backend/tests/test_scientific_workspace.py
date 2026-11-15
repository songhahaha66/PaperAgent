"""Scientific files, one-shot entrypoints and execution diagnostics."""
import asyncio
import hashlib
import json
import pytest

from helpers.context import session_context
from research_tools_helpers import ResearchToolsModel, tool
from ai_system.research.session import run_research
from ai_system.modules.writing.draft import DraftEdit, edit
from ai_system.research.models import ResearchConflict


@pytest.mark.usefixtures('offline_kernel')
def test_agent_edits_source_and_runs_versioned_entrypoints(tmp_path):
    import pypdfium2 as pdfium
    attachment=tmp_path/'attachment/problem.pdf'
    attachment.parent.mkdir()
    with pdfium.PdfDocument.new() as pdf:
        pdf.new_page(600,800);pdf.new_page(600,800);pdf.save(attachment)
    source = '''"""A source file with 'quotes' and a docstring."""
import json, sys
from pathlib import Path
v = json.loads(Path('config/params.json').read_text())['value']
answer = v * int(sys.argv[1])
Path('outputs/answer.json').write_text(json.dumps({'answer': answer}))
print(answer)
'''
    steps = [
        ('research_read_artifact', {'path':'attachment/problem.pdf','pages':[2]}),
        ('workspace_edit', {'path':'code/main.py','content':source}),
        ('workspace_edit', {'path':'config/params.json','content':'{"value":7}'}),
        ('research_run_file', {'path':'code/main.py','args':['2']}),
        ('workspace_edit', {'path':'code/main.py','edits':[{'old_text':'v * int','new_text':'(v + 1) * int'}]}),
        ('research_run_file', {'path':'code/main.py','args':['2']}),
        ('research_pause', {'message':'文件与执行结果已保留。'}),
    ]
    position=0
    def respond(messages):
        nonlocal position
        name,args=steps[position];position+=1
        return tool(name,args,'step-'+str(position))
    ctx=session_context(tmp_path,ResearchToolsModel(respond=respond))
    asyncio.run(run_research(ctx))
    page=json.loads(next(m.content for m in ctx.agent_messages if m.name=='research_read_artifact'))
    assert page['version']==hashlib.sha256(attachment.read_bytes()).hexdigest()
    assert page['total_pages']==2 and page['_images'][0]['page']==2
    runs=list(ctx.fs._manifest['runs'].values())
    assert len(runs)==2 and all(r['status']=='success' for r in runs)
    assert json.loads((tmp_path/'outputs/answer.json').read_text())=={'answer':16}
    assert all(r['entrypoint']=='code/main.py' and r['argv']==['2'] for r in runs)
    assert runs[0]['input_hashes']['code/main.py']!=runs[1]['input_hashes']['code/main.py']
    assert all('config/params.json' in r['input_hashes'] for r in runs)
    assert all(not any('code-history' in p for p in r['input_hashes']) for r in runs)


def test_paper_batch_is_atomic_and_preserves_concurrent_edits(tmp_path):
    ctx=session_context(tmp_path);path=tmp_path/'paper.md'
    original='# 报告\n样本13\n误差2\n';path.write_text(original)
    version=hashlib.sha256(path.read_bytes()).hexdigest()
    async def run():
        invalid=DraftEdit(expected_version=version,edits=[{'old_text':'样本13','new_text':'样本15'},
                                                         {'old_text':'不存在','new_text':'误差1'}])
        with pytest.raises(ResearchConflict):await edit(ctx,invalid.model_dump())
        assert path.read_text()==original
        batch=DraftEdit(expected_version=version,edits=[{'old_text':'样本13','new_text':'样本15'},
                                                       {'old_text':'误差2','new_text':'误差1'}])
        await edit(ctx,batch.model_dump())
        assert '样本15' in path.read_text() and '误差1' in path.read_text()
        path.write_text('人工更新')
        with pytest.raises(ResearchConflict):await edit(ctx,batch.model_dump())
        assert path.read_text()=='人工更新'
    asyncio.run(run())


def test_remote_preparation_records_transfer_and_failure_phases(tmp_path):
    from helpers.sandbox import FakeSession
    from ai_system.sandbox.remote_kernel import RemoteKernel
    from ai_system.sandbox.settings import SandboxSettings
    root=tmp_path/'backend';session=FakeSession(root)
    kernel=RemoteKernel(root,session,SandboxSettings(api_key='offline',host_workspace_root='/server/cache'))
    kernel.fs.write_text('config/params.json','{"value":7}')
    async def run():
        try:
            first=await kernel.execute('first')
            assert first.ok
            record=kernel.fs.get_run_info(first.run_id)
            assert 'config/params.json' in record['input_hashes']
            assert record['transfer']['file_api_calls']['prepare_inputs']==1
            assert record['transfer']['uploaded_bytes']>0 and record['transfer']['downloaded_bytes']>0
            phases=record['phase_timings']
            assert {'lock_wait','sync_inputs','execute','deliver_outputs','archive_outputs'} <= {p['phase'] for p in phases}
            assert all(p['seconds']>=0 and p['finished_at']>=p['started_at'] for p in phases)
            failed=await kernel.execute('error')
            assert not failed.ok and kernel.fs.get_run_info(failed.run_id)['phase_timings']
        finally:await kernel.close()
    asyncio.run(run())
