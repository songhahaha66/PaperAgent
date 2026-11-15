"""Field-level reads and durable chapter drafts, without remote model calls."""
import asyncio
import hashlib
import json

import pytest
from langchain_core.messages import ToolMessage

from ai_system.graph.context import GraphContext
from ai_system.modules.registry import invoke
from ai_system.research.session import run_research, resume
from ai_system.research.models import ResearchConflict
from ai_system.research.planner import working_context
from ai_system.research.runtime import ModelServiceError
from ai_system.research.store import ResearchStore
from helpers.context import session_context
from research_tools_helpers import ResearchToolsModel, tool


def test_json_structure_selection_and_versioned_paging(tmp_path):
    from ai_system.agent.projection import operation_detail
    assert operation_detail(tmp_path,{'tool_name':'research.read_artifact','output':{'json_pointer':'/q2/0/optimum'}})=='已读取 JSON 字段：q2 › 0 › optimum。'
    assert operation_detail(tmp_path,{'tool_name':'research.read_artifact','output':{'json_pointer':'','view':'structure'}})=='已读取 JSON 结构：根节点。'
    ctx=session_context(tmp_path)
    value={'checks':{'error':0},'q2':[{'optimum':{'profit':17.5},'samples':list(range(1000))}],
           'a/b':{'~key':[False,None]}}
    raw=json.dumps(value,ensure_ascii=False)
    ctx.fs.write_text('outputs/answer.json',raw,kind='data')
    async def run():
        overview=await invoke(ctx,'research.read_artifact',path='outputs/answer.json',view='structure')
        fields=json.loads(overview['excerpt'])['fields']
        assert fields['q2']['items'][0]['keys']==['optimum','samples']
        assert fields['a/b']['pointer']=='/a~1b'
        selected=await invoke(ctx,'research.read_artifact',path='outputs/answer.json',json_pointer='/q2/0/optimum')
        assert json.loads(selected['excerpt'])=={'profit':17.5}
        assert selected['version']==overview['version']==hashlib.sha256(raw.encode()).hexdigest()
        for index,expected in enumerate((False,None)):
            result=await invoke(ctx,'research.read_artifact',path='outputs/answer.json',json_pointer=f'/a~1b/~0key/{index}')
            assert json.loads(result['excerpt']) is expected
        with pytest.raises(ValueError,match='checks'):
            await invoke(ctx,'research.read_artifact',path='outputs/answer.json',json_pointer='/validation')
        first=await invoke(ctx,'research.read_artifact',path='outputs/answer.json',json_pointer='/q2/0/samples',limit=40)
        second=await invoke(ctx,'research.read_artifact',path='outputs/answer.json',json_pointer='/q2/0/samples',start=first['next_start'],limit=40)
        full=json.dumps(value['q2'][0]['samples'],ensure_ascii=False,indent=2)
        assert first['excerpt']+second['excerpt']==full[:80] and second['next_start']==80
        assert (await invoke(ctx,'research.read_artifact',path='outputs/answer.json'))['excerpt']==raw
        assert not ctx.kernels._kernels
    asyncio.run(run())


@pytest.mark.parametrize('mode',['markdown','latex'])
def test_chapter_drafts_survive_interruption_and_keep_edit_conflicts(tmp_path,mode):
    main='paper.tex' if mode=='latex' else 'paper.md'
    skeleton='\\documentclass{article}\n\\begin{document}\n\\input{sections/method}\n\\input{sections/results}\n\\end{document}\n'
    initial=[('writing_edit',{'path':main,'content':skeleton if mode=='latex' else '# Report\n\n## Method\nSaved method.\n'})]
    if mode=='latex':initial.append(('writing_edit',{'path':'sections/method.tex','content':'\\section{Method}\nSaved method.\n'}))
    position=0
    def first(messages):
        nonlocal position
        if position==len(initial):raise ModelServiceError('controlled interruption between chapters')
        name,args=initial[position];position+=1
        return tool(name,args,'draft-'+str(position))
    def context(model):
        ctx=GraphContext.create(str(tmp_path),planner_llm=model,output_mode=mode)
        ctx.research=ResearchStore(tmp_path,'incremental-writing')
        ctx.research.initialize('Save a method and result chapter.',output_mode=mode)
        return ctx
    ctx=context(ResearchToolsModel(respond=first))
    with pytest.raises(ModelServiceError,match='controlled interruption'):
        asyncio.run(run_research(ctx))
    saved=(tmp_path/main).read_bytes()
    files={r['path'] for r in working_context(ctx)['document']['source_files']}
    assert main in files and (mode!='latex' or 'sections/method.tex' in files)
    calls=0
    def continued(messages):
        nonlocal calls
        calls+=1
        if calls==1:
            args={'path':'sections/results.tex','content':'\\section{Results}\nThe result is 12.\n'} if mode=='latex' else {
                'path':main,'append':True,'content':'\n## Results\nThe result is 12.\n'}
            return tool('writing_edit',args,'result-chapter')
        assert next(m for m in reversed(messages) if isinstance(m,ToolMessage)).status=='success'
        return tool('research_pause',{'message':'Both chapters are saved.'},'done')
    restored=context(ResearchToolsModel(respond=continued))
    resume(restored.research);asyncio.run(run_research(restored))
    assert calls==2 and not restored.fs._manifest['runs']
    if mode=='latex':
        from ai_system.export.latex import source_diagnostics
        assert (tmp_path/main).read_bytes()==saved
        assert source_diagnostics(tmp_path,main)==[]
        target='sections/results.tex'
    else:
        assert (tmp_path/main).read_bytes().startswith(saved)
        target=main
    current=(tmp_path/target).read_text()
    assert 'The result is 12.' in current
    version=hashlib.sha256(current.encode()).hexdigest()
    (tmp_path/target).write_text(current+'Human correction.\n')
    async def stale_append():
        with pytest.raises(ResearchConflict):
            await invoke(restored,'writing.edit',path=target,append=True,content='stale addition',expected_version=version)
        assert (tmp_path/target).read_text().endswith('Human correction.\n')
    asyncio.run(stale_append())
