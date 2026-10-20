"""Contract errors are repairable model feedback; exhausted analysis is visibly blocked and resumable."""
import asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.graph import llm_utils
from ai_system.graph.context import GraphContext
from ai_system.modules.experiments.analysis import analyze
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
    model_name: str = "controlled-before-repair"
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
    assert not (tmp_path/'plan.md').exists(), "failed analysis must not invent a fixed task plan"
    model.recovered=True
    from ai_system.research.runtime import RepeatedOperationBlocked
    with pytest.raises(RepeatedOperationBlocked):
        asyncio.run(agent.resume_failed())
    assert model.calls == 3, 'same exhausted operation must not silently retry'
    model.model_name = 'controlled-repaired-connection'
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


def test_model_tool_schema_exposes_conditional_constraint_checks():
    from langchain_core.utils.function_calling import convert_to_openai_tool
    schema=convert_to_openai_tool(ProblemAnalysis)['function']['parameters']
    requirement=schema['properties']['requirements']['items']
    # Verify the schema actually sent to the provider, after LangChain removes
    # Pydantic definitions/references. Optional goals must remain supported.
    variants=requirement['anyOf']
    constraint=next(v for v in variants if v['properties']['kind']['enum']==['constraint'])
    assert 'checks' in constraint['required']
    assert constraint['properties']['checks']['minItems']==1
    optional=next(v for v in variants if set(v['properties']['kind']['enum'])=={'goal','deliverable'})
    assert 'checks' not in optional.get('required',[])
    assert requirement['additionalProperties'] is False


def test_missing_checks_from_analysis_trace_repair_preserves_numeric_constraints(tmp_path,monkeypatch):
    """Two omitted checks caused requirements.2 / .4 in the canteen trace."""
    monkeypatch.setattr(llm_utils,'RETRY_DELAY',0)
    task='请计算预测和备餐。售价15元，成本8元，残值2元。预测5天，需求非负。保留代码、图表和论文。'
    first={
        'title':'预测与备餐','restatement':task,'overall_analysis':'先预测再备餐',
        'subproblems':[
            {'id':'Q1','title':'预测','question':'预测需求','kind':'compute','depends_on':[]},
            {'id':'Q2','title':'备餐','question':'基于Q1决策','kind':'compute','depends_on':['Q1']},
        ],
        'requirements':[
            {'id':'R1','kind':'goal','statement':'预测需求','source_quote':'请计算预测和备餐','task_ids':['Q1']},
            {'id':'R2','kind':'goal','statement':'备餐决策','source_quote':'请计算预测和备餐','task_ids':['Q2']},
            {'id':'R3','kind':'constraint','statement':'固定经济参数','source_quote':'售价15元，成本8元，残值2元','task_ids':['Q2']},
            {'id':'R4','kind':'deliverable','statement':'代码图表论文','source_quote':'保留代码、图表和论文','task_ids':['Q1','Q2']},
            {'id':'R5','kind':'constraint','statement':'预测5天且非负','source_quote':'预测5天，需求非负','task_ids':['Q1']},
        ],'assumptions':[],
    }
    repaired=json.loads(json.dumps(first))
    def check(key,task_id,name,operator,expected):
        return {'id':key,'description':name,'left':{'task_id':task_id,'name':name},'operator':operator,'expected':expected}
    repaired['requirements'][2]['checks']=[
        check('price','Q2','price_param','eq',15),check('cost','Q2','cost_param','eq',8),
        check('salvage','Q2','salvage_param','eq',2),
    ]
    repaired['requirements'][4]['checks']=[
        check('length','Q1','forecast_length','eq',5),check('nonnegative','Q1','forecast_min','ge',0),
    ]

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
                    return {'raw':AIMessage(content='',tool_calls=[{'id':'analysis','name':'ProblemAnalysis','args':first}]),
                            'parsed':None,'parsing_error':error}
            assert self.calls==2
            assert json.loads(messages[-2].content)==first
            assert 'requirements.2' in messages[-1].content and 'requirements.4' in messages[-1].content
            return AIMessage(content=json.dumps(repaired,ensure_ascii=False))

    model=Model()
    ctx=GraphContext.create(str(tmp_path),planner_llm=model)
    result=asyncio.run(analyze({'task':task},SimpleNamespace(context=ctx)))
    constraints={r['id']:r for r in result['analysis']['requirements'] if r['kind']=='constraint'}
    assert model.calls==2 and ctx.stage_status['analyze']=='completed'
    assert set(constraints)=={'R3','R5'}
    assert [c['expected'] for c in constraints['R3']['checks']]==[15,8,2]
    assert [c['left']['name'] for c in constraints['R5']['checks']]==['forecast_length','forecast_min']


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


def test_inline_source_data_reaches_design_exploration_solver_and_local_review(tmp_path):
    from ai_system.modules.experiments.planning import pick_methods,propose,critique
    from ai_system.modules.experiments.execution import build_task
    from ai_system.modules.experiments.review import review_prompt
    from test_report_submission import branch
    from test_paper_graph import _Structured,_candidate
    source='销量依次为：420、435、410、460、475。售价 15 元、成本 8 元、剩余残值 2 元。'
    observed=[]
    class Model:
        def with_structured_output(self,schema,**kwargs):
            async def invoke(messages):
                assert source in messages[-1].content
                observed.append(schema.__name__)
                return await _Structured(schema).ainvoke(messages)
            return SimpleNamespace(ainvoke=invoke)
    class Judge(FixedJudge):
        async def ask(self,state,questions):
            assert state['原题']==source
            observed.append('judge')
            return await super().ask(state,questions)
    ctx=GraphContext.create(str(tmp_path),planner_llm=Model(),judge=Judge())
    task={**branch(),'source':source,'analysis':_analysis().model_dump()}
    candidates=[_candidate('one').model_dump(),_candidate('two').model_dump()]
    async def run():
        await pick_methods(ctx,task)
        await propose(ctx,task,['regression'])
        await critique(ctx,task,candidates)
    asyncio.run(run())
    assert {'MethodPick','CandidateSet','judge'}<=set(observed)
    assert source in build_task(task,ctx)
    assert source in review_prompt(task,{},ctx,[])


@pytest.mark.parametrize('reported_works,run_ok',[(False,True),(True,False),(True,True)])
def test_failed_or_ungrounded_explorations_release_kernels_without_promoting_state(tmp_path,monkeypatch,reported_works,run_ok):
    import importlib
    mod=importlib.import_module('ai_system.modules.experiments.exploration')
    from test_report_submission import branch
    task=branch()
    task['design'].update(candidates=[task['design']['chosen']]*2,scores=[{'total':1}]*2,chosen_index=0)
    released=[]
    class Kernels:
        def promote(self,*args):pytest.fail('a failed exploration must never become the formal solver kernel')
        async def release(self,key):released.append(key)
    async def protocol(*args):return {'applicable':True}
    async def probe(ctx,branch,p,protocol):
        p.report={'works':reported_works,'problems':['failed or ungrounded execution']}
        p.messages=[AIMessage(content='broken code context')]
        p.run_log=[{'ok':run_ok}]
    ctx=GraphContext.create(str(tmp_path),planner_llm=None,explore_top_k=2)
    ctx.kernels=Kernels()
    assert asyncio.run(mod.explore(ctx,task)) is None
    assert released==[], 'unsupported non-CSV tasks never open experiment kernels'


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
