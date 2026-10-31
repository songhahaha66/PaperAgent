"""One live Agent reads materials, computes and answers with one project budget."""
import asyncio
import json

import pytest
from langchain_core.messages import ToolMessage
from ai_system.graph.context import GraphContext
from ai_system.research.loop import prepare_action, resume
from ai_system.research.runtime import ResearchRuntime, ModelServiceError
from ai_system.research.store import ResearchStore
from research_tools_helpers import ResearchToolsModel, tool

pytestmark=pytest.mark.usefixtures('offline_kernel')


def make_context(tmp_path,respond):
    ctx=GraphContext.create(str(tmp_path),planner_llm=ResearchToolsModel(respond=respond))
    ctx.research=ResearchStore(tmp_path,'single-agent')
    ctx.research.initialize('读取原始材料，计算总和并给出校验与结果文件。')
    (tmp_path/'paper.md').write_text('# 已有测试稿\n6')
    return ctx


def test_materials_and_six_python_steps_stay_in_one_live_agent(tmp_path):
    attachment=tmp_path/'attachment/input.txt';attachment.parent.mkdir();attachment.write_text('1,2,3')
    qid=None
    def respond(messages):
        nonlocal qid
        runtime.reserve('controlled-model')
        rows=[m for m in messages if isinstance(m,ToolMessage)]
        if not rows:return tool('research_read_artifact',{'path':'attachment/input.txt'},'read')
        last=json.loads(rows[-1].content)
        if len(rows)==1:
            assert '1,2,3' in last['excerpt'] and not ctx.research.snapshot()['experiments']
            return tool('research_create_question',{'question':'数值总和是多少？'},'question')
        if len(rows)==2:qid=last['question_id']
        steps=[m for m in rows if m.name=='research_run_python']
        if len(steps)<6:
            code="from pathlib import Path\nvalue=0\nPath('outputs/intermediate').mkdir(parents=True,exist_ok=True)" if not steps else ''
            code+="\nvalue+=1\nprint('value',value)\nPath('outputs/intermediate/value.txt').write_text(str(value))"
            return tool('research_run_python',{'question_id':qid,'code':code,'title':'累计计算'},str(len(rows)))
        if rows[-1].name!='research_update_answer':
            assert 'value 6' in last['observation']
            return tool('research_update_answer',{'question_id':qid,'statement':'当前累计值为6，校验见输出和结果文件',
                'coverage':'answered'},'answer')
        return tool('research_deliver',{'title':'计算结果','reason':'已完成当前问题'},'deliver')
    ctx=make_context(tmp_path,respond)
    runtime=ResearchRuntime(tmp_path,'single-agent');runtime.configure({'request_limit':20,'compact_paper':False})
    assert not asyncio.run(prepare_action(ctx))
    state=ctx.research.snapshot()
    assert ctx.planner_llm.calls==10 and runtime.status()['used']==10
    assert not state['experiments'] and not state['executions']
    assert len(ctx.fs._manifest['runs'])==6
    assert all(r['question_id']==qid for r in ctx.fs._manifest['runs'].values())
    assert (tmp_path/'outputs/intermediate/value.txt').read_text()=='6'
    assert state['publication']['stage']=='export' and not ctx.kernels._kernels
    assert state['questions'][qid]['stop_reason']==''
    assert not any(a['capability']=='experiments.execute' for a in state['activity'])
    assert not state['artifacts']
    assert all(ctx.fs.exists(r['stdout']) for r in ctx.fs._manifest['runs'].values())
    assert (tmp_path/'code/research_solution.py').is_file()


def test_recovery_reads_saved_files_without_replaying_previous_code(tmp_path):
    qid=None
    def initial(messages):
        nonlocal qid
        rows=[m for m in messages if isinstance(m,ToolMessage)]
        if not rows:return tool('research_create_question',{'question':'计算并保存总和'},'question')
        if len(rows)==1:
            qid=json.loads(rows[-1].content)['question_id']
            return tool('research_run_python',{'question_id':qid,'code':"from pathlib import Path\nPath('outputs/intermediate').mkdir(parents=True,exist_ok=True)\nPath('outputs/intermediate/base.txt').write_text('6')\nprint(6)"},'code')
        raise ModelServiceError('controlled model interruption')
    ctx=make_context(tmp_path,initial)
    assert asyncio.run(prepare_action(ctx))
    from copy import deepcopy
    before=deepcopy(ctx.fs._manifest['runs']);assert len(before)==1
    def resumed(messages):
        assert any('base.txt' in str(m.content) for m in messages), '原会话必须保留'
        rows=[m for m in messages if isinstance(m,ToolMessage) and m.tool_call_id not in {'question','code'}]
        if not rows:return tool('research_run_python',{'question_id':qid,
            'code':"from pathlib import Path\nv=int(Path('outputs/intermediate/base.txt').read_text())\nassert v==6\nprint(v*2)"},'new-code')
        if len(rows)==1:
            assert '12' in json.loads(rows[-1].content)['observation']
            return tool('research_update_answer',{'question_id':qid,'statement':'派生值为12','coverage':'answered','stop_reason':'从保存的6计算完成'},'answer')
        return tool('research_deliver',{'title':'恢复结果'},'deliver')
    resume(ctx.research);ctx.planner_llm=ResearchToolsModel(respond=resumed)
    assert not asyncio.run(prepare_action(ctx))
    after=ctx.research.snapshot()
    assert not after['executions'] and len(ctx.fs._manifest['runs'])==2
    for key,receipt in before.items():assert ctx.fs.get_run_info(key)==receipt


def test_binary_materials_are_readable_before_any_experiment(tmp_path):
    import pandas as pd
    from ai_system.modules.registry import invoke
    ctx=make_context(tmp_path,lambda messages:None)
    folder=tmp_path/'attachment';folder.mkdir(exist_ok=True)
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
             b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> >>']
    raw=b'%PDF-1.4\n';offsets=[]
    for index,obj in enumerate(objects,1):
        offsets.append(len(raw));raw+=f'{index} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(raw);raw+=b'xref\n0 4\n0000000000 65535 f \n'
    raw+=b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets)
    raw+=f'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    (folder/'source.pdf').write_bytes(raw)
    pd.DataFrame({'power_kW':[10,20]}).to_excel(folder/'data.xlsx',index=False)
    async def read():
        pdf=await invoke(ctx,'research.read_artifact',path='attachment/source.pdf')
        book=await invoke(ctx,'research.read_artifact',path='attachment/data.xlsx')
        assert '第1页' in pdf['excerpt'] and '无可提取文本' in pdf['excerpt']
        assert 'power_kW' in book['excerpt'] and '20' in book['excerpt']
    asyncio.run(read())
    state=ctx.research.snapshot()
    assert not state['experiments'] and not state['executions'] and not ctx.kernels._kernels


def test_download_retry_is_automatic_and_computes_once(tmp_path):
    from helpers.sandbox import FakeSession
    from ai_system.sandbox.remote_kernel import RemoteKernel
    from ai_system.sandbox.settings import SandboxSettings
    from ai_system.modules.registry import invoke
    class FilesFailOnce(FakeSession):
        failed=False
        async def download(self,path,destination,max_bytes):
            if not self.failed:
                self.failed=True
                raise ConnectionError('controlled file channel interruption')
            await super().download(path,destination,max_bytes)
    ctx=make_context(tmp_path,lambda messages:None)
    session=FilesFailOnce(tmp_path)
    ctx.kernels._kernels['research']=RemoteKernel(tmp_path,session,SandboxSettings(provider='fake'),fs=ctx.fs)
    async def run():
        q=await invoke(ctx,'research.update_question',question='保存计算结果')
        try:
            result=await invoke(ctx,'research.run_python',question_id=q['question_id'],code='computed once')
            assert session.calls==['computed once'] and session.failed
            assert '文件交付尚未完成' not in result['observation']
            state=ctx.research.snapshot();run=ctx.fs.get_run_info(result['run_id'])
            assert run['status']=='success' and not run['delivery_error']
            assert not state['experiments'] and not state['artifacts']
            assert any(m.get('source_run')==result['run_id'] for m in ctx.fs._manifest['files'].values())
            assert (tmp_path/'outputs/table.csv').read_text()=='computed once'
        finally:await ctx.kernels.close()
    asyncio.run(run())


def test_direct_run_freezes_original_materials_without_experiment_scope(tmp_path):
    from ai_system.modules.registry import invoke
    ctx=make_context(tmp_path,lambda messages:None)
    folder=tmp_path/'attachment';folder.mkdir(exist_ok=True)
    (folder/'input.txt').write_text('6')
    async def run():
        try:
            return await invoke(ctx,'research.run_python',
                code="from pathlib import Path\nprint(Path('attachment/input.txt').read_text())")
        finally:await ctx.kernels.close()
    result=asyncio.run(run());rid=result['run_id']
    assert not ctx.research.snapshot()['questions'] and not ctx.research.snapshot()['experiments']
    frozen=ctx.fs.run_dir(rid)/'inputs/attachment/input.txt'
    assert frozen.read_text()=='6'
    (folder/'input.txt').write_text('corrected')
    assert frozen.read_text()=='6'
