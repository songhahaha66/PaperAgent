"""Real execution/files flow to reports without registration or acceptance tools."""
import asyncio

import pytest
from ai_system.graph.context import GraphContext
from ai_system.modules.experiments.execution import build_tools
from ai_system.research.store import ResearchStore

pytestmark=pytest.mark.usefixtures('offline_kernel')


def test_python_assertions_still_fail_and_keep_the_execution_record(tmp_path):
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    runs=[]
    async def run():
        try:return await next(t for t in build_tools(ctx,'Q1',runs) if t.name=='run_python').ainvoke({'code':"x=3\nassert x == 4, 'capacity mismatch'"})
        finally:await ctx.kernels.close()
    output=asyncio.run(run())
    assert 'capacity mismatch' in output and '执行失败' in output
    assert len(runs)==1 and not runs[0]['ok']
    info=ctx.fs.get_run_info(runs[0]['run_id'])
    assert info['status']=='error' and ctx.fs.exists(info['stdout'])


def test_markdown_checks_real_image_files_without_a_figure_id(tmp_path):
    from ai_system.modules.writing.validation import check_sections
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    store=ResearchStore(tmp_path,'images');store.initialize('保存图像');ctx.research=store
    (tmp_path/'outputs').mkdir(exist_ok=True)
    (tmp_path/'outputs/chart.png').write_bytes(b'fixture image')
    state={'analysis':{'questions':[]},'outline':[{'key':'abstract','title':'摘要'}],
           'sections':{'abstract':'![实际图](outputs/chart.png)','references':'无外部文献'}}
    assert check_sections(state,ctx)==[]
    state['sections']['abstract']='![不存在的图](outputs/missing.png)'
    issues=check_sections(state,ctx)
    assert len(issues)==1 and '图片文件不可用' in issues[0]['message']
