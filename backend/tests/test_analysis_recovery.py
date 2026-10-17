"""Contract errors are repairable model feedback; exhausted analysis is visibly blocked and resumable."""
import asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.graph import llm_utils
from ai_system.graph.context import GraphContext
from ai_system.graph.nodes.analyze import analyze
from ai_system.graph.state import ProblemAnalysis
from ai_system.graph.agent import PaperGraphAgent
from ai_system.research.models import DomainCheck,LinearModel
from ai_system.research.store import ResearchStore
from test_paper_graph import FakeModel,FixedJudge,_analysis,TASK


def invalid_plan(case):
    plan=_analysis().model_dump()
    if case=='coverage':
        plan['requirements'][0]['task_ids']=['Q9']
    elif case=='scope':
        plan['requirements'][0]['checks']=[DomainCheck(id='wrong_scope',description='finite',left={'task_id':'Q2','name':'x'},operator='finite').model_dump()]
    elif case=='quote':
        plan['requirements'][0]['source_quote']='invented source quote'
    elif case=='linear':
        plan['requirements'][0]['checks']=[DomainCheck(id='wrong_linear',description='inapplicable linear model',left={'task_id':'Q1','name':'objective'},operator='linear_feasible',linear_model=LinearModel(variables=[{'name':'x','source_quote':TASK}],objective=[1],objective_quote='invented optimization objective')).model_dump()]
    else:
        plan['subproblems'][0]['depends_on']=['Q2']
        plan['subproblems'][1]['depends_on']=['Q1']
    return plan


@pytest.mark.parametrize('case,feedback',[('coverage','未知任务'),('scope','wrong_scope'),('quote','来源摘录'),('linear','wrong_linear'),('cycle','依赖存在环')])
def test_analysis_contract_failures_use_existing_bounded_retry_and_actual_candidate(tmp_path,monkeypatch,case,feedback):
    monkeypatch.setattr(llm_utils,'RETRY_DELAY',0)
    class Model:
        calls=0
        messages=[]
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,messages):
            self.calls+=1;self.messages=messages
            if self.calls==1:return ProblemAnalysis.model_validate(invalid_plan(case))
            assert feedback in messages[-1].content
            assert isinstance(messages[-2],AIMessage)
            assert json.loads(messages[-2].content)==ProblemAnalysis.model_validate(invalid_plan(case)).model_dump()
            return AIMessage(content=_analysis().model_dump_json())
    model=Model()
    ctx=GraphContext.create(str(tmp_path),planner_llm=model)
    result=asyncio.run(analyze({'task':TASK},SimpleNamespace(context=ctx)))
    assert model.calls==2
    assert {sp['id'] for sp in result['analysis']['subproblems']}=={'Q1','Q2','Q3'}
    assert ctx.stage_status['analyze']=='completed'


class FailingAnalysis(FakeModel):
    recovered:bool=False
    calls:int=0
    def with_structured_output(self,schema,**kwargs):
        if schema is ProblemAnalysis and not self.recovered:
            async def reply(messages):
                self.calls+=1
                return ProblemAnalysis.model_validate(invalid_plan('coverage'))
            return SimpleNamespace(ainvoke=reply)
        return super().with_structured_output(schema,**kwargs)
    def _respond(self,messages):
        if not self.recovered and 'JSON Schema' in messages[-1].content:
            self.calls+=1
            return AIMessage(content=json.dumps(invalid_plan('coverage'),ensure_ascii=False))
        return super()._respond(messages)


def test_exhausted_analysis_marks_failed_then_resumes_original_question_without_business_state(tmp_path,monkeypatch):
    monkeypatch.setattr(llm_utils,'RETRY_DELAY',0)
    events=[]
    class Stream:
        async def send_json_block(self,kind,content):events.append((kind,content))
    model=FailingAnalysis(loose_number_once=False)
    agent=PaperGraphAgent(model,workspace_dir=str(tmp_path),judge=FixedJudge(),stream_manager=Stream(),explore_top_k=1,max_review_rounds=0,check_claims=False)
    assert '任务执行失败' in asyncio.run(agent.run(TASK))
    thread=json.loads((tmp_path/'.system/graph_thread.json').read_text())
    assert thread['status']=='failed' and model.calls==3
    assert ResearchStore(tmp_path,thread['thread_id'],read_only=True).snapshot() is None
    assert any(kind=='paper_phase' and c['stage']=='analyze' and c['status']=='blocked' for kind,c in events)
    assert '阻塞' in (tmp_path/'plan.md').read_text()
    model.recovered=True
    assert 'paper.md' in asyncio.run(agent.resume_failed())
    after=json.loads((tmp_path/'.system/graph_thread.json').read_text())
    assert after['thread_id']==thread['thread_id'] and after['status']=='done'
    state=ResearchStore(tmp_path,thread['thread_id'],read_only=True).snapshot()
    assert state['spec']['requirement']==TASK


def test_contract_feedback_reports_multiple_defects_together():
    from ai_system.research.validation import validate_spec
    data=invalid_plan('scope')
    data['requirements'][1]['task_ids']=['Q9']
    data['requirements'][2]['source_quote']='invented quote'
    with pytest.raises(ValueError) as error:
        validate_spec(TASK,data)
    assert all(text in str(error.value) for text in ['wrong_scope','Q9','来源摘录'])


@pytest.mark.parametrize('source,quote,expected',[
    ('最后 5 天验证误差，售价 15 元','最后5天验证误差','最后 5 天验证误差'),
    ('售价 15 元','售价14元',None),
    ('序列 1 5','序列15',None),
    ('not possible','notpossible',None),
    ('值 0 . 5','值0.5',None),
])
def test_source_excerpt_preserves_original_whitespace_without_merging_numbers_or_words(source,quote,expected):
    from ai_system.research.validation import source_excerpt
    assert source_excerpt(source,quote)==expected


def test_analysis_freezes_original_spans_instead_of_rejecting_spacing(tmp_path):
    class Model:
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,messages):
            candidate=_analysis()
            for r in candidate.requirements:r.source_quote=r.source_quote.replace(' ','')
            return candidate
    ctx=GraphContext.create(str(tmp_path),planner_llm=Model())
    result=asyncio.run(analyze({'task':TASK},SimpleNamespace(context=ctx)))
    assert all(r['source_quote'] in TASK for r in result['analysis']['requirements'])


def test_schema_repairs_keep_raw_tool_and_json_candidates_with_bounded_history(monkeypatch):
    monkeypatch.setattr(llm_utils,'RETRY_DELAY',0)
    first=_analysis().model_dump()
    first['requirements'][0].update(kind='constraint',checks=[])
    second=invalid_plan('linear')
    second['requirements'][0]['checks'][0]['linear_model']['objective']=[]
    class Model:
        calls=0
        def with_structured_output(self,schema,**kwargs):
            assert kwargs['include_raw'] is True
            return self
        async def ainvoke(self,messages):
            self.calls+=1
            if self.calls==1:
                try:ProblemAnalysis.model_validate(first)
                except ValueError as error:
                    raw=AIMessage(content='',tool_calls=[{'id':'analysis','name':'ProblemAnalysis','args':first}])
                    return {'raw':raw,'parsed':None,'parsing_error':error}
            candidate=messages[-2]
            assert isinstance(candidate,AIMessage) and not candidate.tool_calls
            assert len(messages)==3
            assert json.loads(candidate.content)==(first if self.calls==2 else second)
            assert ('必须有可执行检查' if self.calls==2 else '数量一致') in messages[-1].content
            return AIMessage(content=json.dumps(second) if self.calls==2 else _analysis().model_dump_json())
    model=Model()
    result=asyncio.run(llm_utils.call_structured(model,ProblemAnalysis,[AIMessage(content='plan')]))
    assert model.calls==3 and result==_analysis()
