"""Real execution/files flow to reports without registration or acceptance tools."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from ai_system.graph.context import GraphContext
from ai_system.graph.state import SolveReport
from ai_system.modules.experiments.execution import _solve_loop, build_tools, build_task
from ai_system.research.control import execute, experiment_dispatch
from ai_system.research.store import ResearchStore
from ai_system.research.views import execution_context
from services.file_services.workspace_layout import workspace_path
from helpers.graph import FakeModel
from research_tools_helpers import add_experiment, answer

pytestmark=pytest.mark.usefixtures('offline_kernel')


class FileModel(FakeModel):
    code: str = "from pathlib import Path\nimport json\nanswer=6\nassert answer == sum([1,2,3])\nPath('outputs/intermediate').mkdir(parents=True,exist_ok=True)\nPath('outputs/intermediate/result.json').write_text(json.dumps({'answer':answer}))\nprint('answer',answer)"
    def _solver_step(self,task,messages):
        observed=[m for m in messages if isinstance(m,ToolMessage)]
        call=('SolveReport',{'summary':'求和结果为6，结果保存在outputs/intermediate/result.json',
              'approach':'直接求和','validation':'assert验证求和为6','assumptions':['固定输入为1、2、3']}) if observed else ('run_python',{'code':self.code})
        return AIMessage(content='',tool_calls=[{'name':call[0],'args':call[1],'id':str(len(messages))}])


def test_plain_output_and_file_submit_once_without_registration(tmp_path):
    model=FileModel()
    ctx=GraphContext.create(str(tmp_path),planner_llm=model,max_solver_steps=8)
    runs=[]
    async def run():
        try:return await _solve_loop(ctx,{'subproblem':{'id':'Q1'}},runs,[HumanMessage(content='开始求解 # 子问题 Q1')])
        finally:await ctx.kernels.close()
    status,error,report,_=asyncio.run(run())
    assert status=='completed' and error is None
    assert '6' in report['summary'] and report['validation']
    assert len(runs)==1 and runs[0]['ok']
    assert json.loads((tmp_path/'outputs/intermediate/result.json').read_text())=={'answer':6}
    assert ctx.registry.results=={} and ctx.registry.claims=={}
    transcript=json.loads(workspace_path(tmp_path,'.system/transcripts/Q1.json').read_text())
    calls=[c['name'] for m in transcript for c in m.get('data',{}).get('tool_calls',[])]
    assert calls==['run_python','SolveReport']


def test_execution_commits_files_and_report_without_scalar_ledger(tmp_path):
    ctx=GraphContext.create(str(tmp_path),planner_llm=FileModel())
    store=ResearchStore(tmp_path,'file-results');store.initialize('计算1、2、3之和，保存结果文件')
    ctx.research=store
    sid=add_experiment(store,'求和',method='直接计算并保存JSON')
    async def run():
        try:return await execute(experiment_dispatch(ctx,sid),SimpleNamespace(context=ctx))
        finally:await ctx.kernels.close()
    result=asyncio.run(run());state=store.snapshot()
    assert result['_execution_outcome']['status']=='completed'
    task=state['experiments'][sid]
    assert task['execution_status']=='succeeded' and task['acceptance']=='accepted'
    assert state['validations']==[]
    assert not any(a['type']=='result' for a in state['artifacts'].values())
    record=execution_context(store)[0]
    assert record['outputs'] and any('answer 6' in x['excerpt'] for x in record['outputs'])
    assert record['record']['files']
    path=record['record']['files'][0]
    assert (tmp_path/path).is_file()
    answer(store,question_id=task['question_id'],statement='求和结果为6',coverage='answered',stop_reason='已计算并校验')
    assert store.delivery_ready(store.snapshot())


def test_removed_tools_and_evidence_fields_are_not_model_visible(tmp_path):
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    assert {t.name for t in build_tools(ctx,'Q1')}=={'run_python','read_file','search_literature','retry_outputs'}
    properties=SolveReport.model_json_schema()['properties']
    assert not {'requirement_evidence','controls','result_refs'} & properties.keys()


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
