"""Structured-output repair and saved source-contract validation."""
import asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.graph import llm_utils
from ai_system.research.models import DomainCheck,LinearModel
from helpers.graph import ProblemAnalysis, _analysis, TASK


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


def test_schema_repairs_keep_raw_tool_and_json_candidates_with_bounded_history(monkeypatch):
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


def test_dispatch_uses_committed_original_source_for_initial_and_recovered_workers(tmp_path):
    from ai_system.research.control import experiment_dispatch
    from helpers.contracts import store_for,TASK as original
    store=store_for(tmp_path);ctx=SimpleNamespace(research=store)
    assert all(experiment_dispatch(ctx,key)['source'].rstrip('\n')==original for key in ('Q1','Q3'))
    store.claim('Q1');store.recover()
    assert all(experiment_dispatch(ctx,key)['source'].rstrip('\n')==original for key in ('Q1','Q3'))


@pytest.mark.parametrize('raw_tool',[True,False])
def test_truncated_reply_stops_without_replaying_partial_json_or_retrying_same_limit(monkeypatch,raw_tool):
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
            assert all(partial not in str(message.content) for message in messages)
            assert '被截断' in messages[-1].content and '缩短' in messages[-1].content
            return AIMessage(content=_analysis().model_dump_json())
    model=Model()
    from ai_system.config.generation import ModelGenerationLimit
    with pytest.raises(ModelGenerationLimit,match='截断'):
        asyncio.run(llm_utils.call_structured(model,ProblemAnalysis,[]))
    assert model.calls==(1 if raw_tool else 2)
