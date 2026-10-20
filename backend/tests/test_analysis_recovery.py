"""Structured-output repair and saved source-contract validation."""
import asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.graph import llm_utils
from ai_system.graph.context import GraphContext
from ai_system.graph.agent import PaperGraphAgent
from ai_system.research.models import DomainCheck,LinearModel
from ai_system.research.store import ResearchStore
from test_paper_graph import FakeModel,FixedJudge,ProblemAnalysis,_analysis,TASK


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


def test_schema_repairs_keep_raw_tool_and_json_candidates_with_bounded_history(monkeypatch):
    monkeypatch.setattr(llm_utils,'RETRY_DELAY',0)
    first=_analysis().model_dump()
    first['requirements'][0]['source_quote']=''
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
            assert ('at least 1 character' if self.calls==2 else '数量一致') in messages[-1].content
            return AIMessage(content=json.dumps(second) if self.calls==2 else _analysis().model_dump_json())
    model=Model()
    result=asyncio.run(llm_utils.call_structured(model,ProblemAnalysis,[AIMessage(content='plan')]))
    assert model.calls==3 and result==_analysis()


def test_changed_schema_can_retry_without_erasing_old_guards_or_request_usage(tmp_path):
    from pydantic import create_model
    from ai_system.research.runtime import ResearchRuntime,budget_scope,RepeatedOperationBlocked
    old=create_model('Reply',legacy=(str,...))
    fixed=create_model('Reply',answer=(str,...))
    runtime=ResearchRuntime(tmp_path)
    class Model:
        calls=0
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,messages):
            self.calls+=1;runtime.reserve('fake-model')
            return {'answer':'ok'}
    model=Model()
    with budget_scope(tmp_path,'study'):
        for _ in range(3):
            with pytest.raises(RuntimeError):
                asyncio.run(llm_utils.call_structured(model,old,[],retries=0))
        with pytest.raises(RepeatedOperationBlocked):
            asyncio.run(llm_utils.call_structured(model,old,[],retries=0))
        assert asyncio.run(llm_utils.call_structured(model,fixed,[],retries=0)).answer=='ok'
        with pytest.raises(RepeatedOperationBlocked):
            asyncio.run(llm_utils.call_structured(model,old,[],retries=0))
    assert model.calls==runtime.status()['used']==4


def test_dispatch_uses_committed_original_source_for_initial_and_recovered_workers(tmp_path):
    from ai_system.research.control import task_dispatch as dispatch
    from test_research_p2 import store_for,problem,TASK as original
    store=store_for(tmp_path)
    runtime=SimpleNamespace(context=SimpleNamespace(research=store))
    state={'task':'shortened summary without source data','analysis':problem().model_dump()}
    for sends in [dispatch(state,runtime,['Q1','Q3'])]:
        assert sends and all(s.arg['source']==original for s in sends)
    store.claim('Q1')
    store.recover()
    assert all(s.arg['source']==original for s in dispatch(state,runtime,['Q1','Q3']))


def test_inline_source_data_reaches_solver_and_local_review(tmp_path):
    from ai_system.modules.experiments.execution import build_task
    from ai_system.modules.experiments.review import review_prompt
    from test_report_submission import branch
    source='销量依次为：420、435、410、460、475。售价 15 元、成本 8 元、剩余残值 2 元。'
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    task={**branch(),'source':source,'analysis':_analysis().model_dump()}
    assert source in build_task(task,ctx)
    assert source in review_prompt(task,{},ctx,[])





@pytest.mark.parametrize('quote,expected,valid',[
    ('比较两种方法，用最后 5 天验证误差',20,False),
    ('误差不超过 15%',0.15,True),
    ('误差不超过 15%',15,False),
    ('容量不超过 100',10,False),
    ('售价 15 元',15,True),
    ('售价15元',15,True),
    ('备餐量非负',0,True),
])
def test_requirement_thresholds_cannot_be_invented_or_borrowed_from_other_source_numbers(quote,expected,valid):
    from ai_system.research.validation import validate_spec
    from ai_system.research.models import Requirement
    plan=_analysis().model_dump()
    plan['requirements'][0]=Requirement(id='R1',kind='constraint',statement='numeric check',source_quote=quote,task_ids=['Q1'],
        checks=[DomainCheck(id='bound',description='fixed bound',left={'task_id':'Q1','name':'metric'},operator='le',expected=expected)]).model_dump()
    source=TASK+' 原题另有 20 个观测。'+quote
    if valid:validate_spec(source,plan)
    else:
        with pytest.raises(ValueError,match='数值依据'):validate_spec(source,plan)


def test_structured_output_and_its_json_retry_share_a_bounded_output_limit(monkeypatch):
    from langchain_openai import ChatOpenAI
    monkeypatch.setattr(llm_utils,'RETRY_DELAY',0)
    original=ChatOpenAI(model='test',api_key='local-test',max_tokens=16000)
    seen=[]
    class Structured:
        async def ainvoke(self,messages):return None
    def bind(llm,schema,**kwargs):
        seen.append(llm.max_tokens)
        return Structured()
    async def repair(llm,schema,messages,error=None):
        seen.append(llm.max_tokens)
        return _analysis()
    monkeypatch.setattr(llm_utils,'structured_model',bind)
    monkeypatch.setattr(llm_utils,'_ask_json',repair)
    result=asyncio.run(llm_utils.call_structured(original,ProblemAnalysis,[]))
    assert result==_analysis() and seen==[6000,6000] and original.max_tokens==16000


@pytest.mark.parametrize('kind',['text','tool','invalid_tool'])
def test_missing_parsed_and_error_retains_only_public_candidate_for_repair(monkeypatch,kind):
    from services.chat_services.paper_comments import CommentReply
    monkeypatch.setattr(llm_utils,'RETRY_DELAY',0)
    candidate={'replacement':'原候选片段'}
    partial=json.dumps(candidate,ensure_ascii=False)
    kwargs={'content':partial if kind=='text' else '',
            'additional_kwargs':{'reasoning_content':'private reasoning'},
            'response_metadata':{'headers':{'authorization':'private header'}}}
    if kind=='tool':kwargs['tool_calls']=[{'name':'CommentReply','args':candidate,'id':'one'}]
    if kind=='invalid_tool':kwargs['invalid_tool_calls']=[{'name':'CommentReply','args':partial,'id':'one','error':'invalid'}]
    class Model:
        calls=0
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,messages):
            self.calls+=1
            if self.calls==1:return {'raw':AIMessage(**kwargs),'parsed':None,'parsing_error':None}
            assert messages[-2].content==partial
            assert not messages[-2].additional_kwargs and not messages[-2].response_metadata
            assert 'private reasoning' not in str(messages) and 'private header' not in str(messages)
            return AIMessage(content=json.dumps({'answer':'说明','replacement':'原候选片段'},ensure_ascii=False))
    model=Model()
    assert asyncio.run(llm_utils.call_structured(model,CommentReply,[],retries=1))==CommentReply(answer='说明',replacement='原候选片段')
    assert model.calls==2


@pytest.mark.parametrize('raw_tool',[True,False])
def test_truncated_structured_outputs_keep_partial_candidate_and_request_shorter_complete_json(monkeypatch,raw_tool):
    monkeypatch.setattr(llm_utils,'RETRY_DELAY',0)
    partial='{"title":"unfinished","requirements":[{"id":"R1"}]'
    class Model:
        calls=0
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,messages):
            self.calls+=1
            if self.calls==1:
                if not raw_tool:return None
                reply=AIMessage(content='',response_metadata={'finish_reason':'length'},invalid_tool_calls=[
                    {'name':'ProblemAnalysis','args':partial,'id':'bad','error':'truncated'}])
                return {'raw':reply,'parsed':None,'parsing_error':ValueError('missing fields')}
            if not raw_tool and self.calls==2:
                return AIMessage(content=partial,response_metadata={'finish_reason':'length'})
            assert messages[-2].content==partial
            assert '被截断' in messages[-1].content and '缩短' in messages[-1].content
            return AIMessage(content=_analysis().model_dump_json())
    model=Model()
    assert asyncio.run(llm_utils.call_structured(model,ProblemAnalysis,[]))==_analysis()
    assert model.calls==(2 if raw_tool else 3)
