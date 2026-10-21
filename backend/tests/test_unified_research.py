import asyncio,json,sys
from pathlib import Path
from uuid import uuid4
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.research.runtime import ResearchRuntime,budget_scope,request_hook,response_hook,ResearchBudgetExhausted
from ai_system.graph.tracing import LLMCallTrace
from test_experiences import env,submit,TASK,adopt_ready
from ai_system.graph.context import GraphContext
from ai_system.modules.experiments import planning as design
from ai_system.research.experience import prompt_context,design_usage


def test_actual_send_budget_is_shared_durable_and_stops_before_transport(tmp_path):
    runtime=ResearchRuntime(tmp_path);runtime.configure({'request_limit':2,'timeout_seconds':30,'compact_paper':True,'confirm_models':True})
    sent=[]
    def handler(request):sent.append(request);return httpx.Response(200,json={'ok':True})
    with httpx.Client(transport=httpx.MockTransport(handler),event_hooks={'request':[request_hook],'response':[response_hook]}) as client:
        for rid in ('first','resume'):
            with budget_scope(tmp_path,rid):assert client.post('https://aigw.saurlax.com/v1/chat/completions').status_code==200
        with budget_scope(tmp_path,'resume'):
            with pytest.raises(ResearchBudgetExhausted):client.post('https://aigw.saurlax.com/v1/chat/completions')
    assert len(sent)==2 and ResearchRuntime(tmp_path,'new-id').status()['used']==2
    assert all(set(r)<={'sequence','sent_at','host','label','research_id','state','http_status','headers_at'} for r in runtime.requests())


def test_rate_limit_response_is_not_a_permanent_global_quota_block(tmp_path):
    runtime=ResearchRuntime(tmp_path);sent=[]
    def handler(request):sent.append(1);return httpx.Response(429,json={})
    with httpx.Client(transport=httpx.MockTransport(handler),event_hooks={'request':[request_hook],'response':[response_hook]}) as client:
        with budget_scope(tmp_path,'study'):
            client.post('https://aigw.saurlax.com/v1/chat/completions')
            assert client.post('https://aigw.saurlax.com/v1/chat/completions').status_code==429
    assert sent==[1,1] and runtime.status()['state']=='active'


def test_usage_unknown_is_explicit_and_resume_trace_is_not_overwritten(tmp_path):
    from langchain_core.outputs import LLMResult,ChatGeneration
    from langchain_core.messages import AIMessage
    path=tmp_path/'calls.json'
    for _ in range(2):
        trace=LLMCallTrace();rid=uuid4();trace.on_chat_model_start({},[[]],run_id=rid)
        trace.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content='answer'))]]),run_id=rid)
        trace.dump(path)
    record=json.loads(path.read_text());assert len(record['calls'])==2
    assert all(c['usage_returned'] is False for c in record['calls'])


def test_confirmation_design_really_receives_history_then_freezes_its_refs(env):
    a=env.store('A');submit(a);adopt_ready(env.scope());b=env.store('B')
    # The confirmation design happens before claim; a later worker freezes it.
    ctx=GraphContext.create(str(b.root),planner_llm=None,experience_scope=env.scope('B'));ctx.research=b
    branch={'source':TASK,'subproblem':{'id':'Q1','question':TASK}}
    text=prompt_context(ctx,branch,['linear_programming'],'design');assert '历史研究经验' in text
    saved=design_usage(b,'Q1');assert saved[0]['refs']
    order=b.claim('Q1');b.bind_experiences(order,saved[0]['refs']);assert order.experience_refs


def test_uploaded_csv_in_attachment_is_eligible_for_comparison(tmp_path):
    from ai_system.modules.experiments.supervised import csv_files,prepare
    from ai_system.modules.experiments.store import SupervisedSpec
    folder=tmp_path/'attachment';folder.mkdir();(folder/'sample.csv').write_text('x,target\n'+'\n'.join(f'{i},{i*2}' for i in range(40)))
    spec=SupervisedSpec(dataset='attachment/sample.csv',features=['x'],target='target').model_dump()
    assert csv_files(tmp_path)==[folder/'sample.csv']
    frozen,*_=prepare(tmp_path,spec);assert len(frozen['train_ids'])==32 and len(frozen['validation_ids'])==8


def test_final_render_refuses_unchecked_citation_before_publishing(tmp_path):
    from types import SimpleNamespace
    from ai_system.modules.writing.validation import render
    from test_literature import seed
    from test_research_p2 import problem, TASK
    _,_,evidence=seed(tmp_path)
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    from test_native_latex import accepted_store
    ctx.research=accepted_store(tmp_path)
    state={'analysis':problem().model_dump(),'task':TASK,
           'sections':{'restatement':'未经核查的主张 {{C:'+evidence['id']+'}}'}}
    with pytest.raises(ValueError,match='发布前文献引用未获原文支持'):
        asyncio.run(render(state,SimpleNamespace(context=ctx)))
    assert not (tmp_path/'paper.md').exists()
    assert (tmp_path/'.system/paper_raw.md').exists()


def test_actual_estimator_guard_rejects_misnamed_methods_and_parameters():
    from sklearn.linear_model import LinearRegression,Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler,PolynomialFeatures
    from ai_system.modules.experiments.contracts import candidate_contract,check_estimator
    ols=candidate_contract({'name':'OLS基线','idea':'使用LinearRegression'})
    ridge=candidate_contract({'name':'标准化Ridge','idea':'使用StandardScaler与Ridge(alpha=1)'})
    assert check_estimator(LinearRegression(),ols)['family']=='ols'
    assert check_estimator(make_pipeline(StandardScaler(),Ridge(alpha=1)),ridge)['checked']
    for estimator,contract in [(make_pipeline(PolynomialFeatures(),Ridge()),ols),
                               (make_pipeline(StandardScaler(),Ridge(alpha=2)),ridge),
                               (Ridge(alpha=1),ridge)]:
        with pytest.raises(ValueError,match='方法契约不符'):check_estimator(estimator,contract)


def test_cancelled_graph_records_paused_checkpoint_without_resetting_budget(tmp_path):
    from types import SimpleNamespace
    from ai_system.graph.agent import PaperGraphAgent
    class Graph:
        async def aget_state(self,config):return SimpleNamespace(values={})
        async def ainvoke(self,*args,**kwargs):raise asyncio.CancelledError()
    runtime=ResearchRuntime(tmp_path);runtime.reserve('example.test')
    agent=PaperGraphAgent(None,workspace_dir=str(tmp_path),judge=None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent._invoke(Graph(),None,{},'same-study',SimpleNamespace()))
    saved=json.loads((tmp_path/'.system/graph_thread.json').read_text())
    assert saved['status']=='paused' and saved['thread_id']=='same-study'
    assert runtime.status()['used']==1


def test_parallel_writing_preserves_finished_sections_when_another_call_fails(tmp_path,monkeypatch):
    from test_native_latex import accepted_store
    from ai_system.modules.writing import sections as writing
    store=accepted_store(tmp_path);ctx=GraphContext.create(str(tmp_path),planner_llm=None);ctx.research=store
    async def section(ctx,state,key,title):
        if key=='analysis':raise ResearchBudgetExhausted('budget reached')
        return '已完成且尚未发布的章节。'
    monkeypatch.setattr(writing,'write_section',section)
    monkeypatch.setattr(writing,'section_request',lambda *args:'stable writing inputs')
    with pytest.raises(ResearchBudgetExhausted):
        asyncio.run(writing.write_many(ctx,{},[('restatement','重述'),('analysis','分析')]))
    state=store.snapshot()
    assert state['sections']['restatement']['text']=='已完成且尚未发布的章节。'
    assert state['sections']['restatement']['context_hash'] and not state.get('paper_hash')


def test_real_langchain_transport_hook_and_total_timeout(tmp_path):
    from ai_system.core_handlers.llm_providers import AIGWChatOpenAI
    from ai_system.research.runtime import async_request_hook,async_response_hook
    async def work():
        runtime=ResearchRuntime(tmp_path);runtime.configure({'request_limit':2,'timeout_seconds':.05,'compact_paper':True,'confirm_models':False})
        async def slow(request):
            await asyncio.sleep(.2)
            return httpx.Response(200,json={'id':'r','object':'chat.completion','created':0,'model':'step-5-preview','choices':[{'index':0,'message':{'role':'assistant','content':'done'},'finish_reason':'stop'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow),event_hooks={'request':[async_request_hook],'response':[async_response_hook]}) as client:
            model=AIGWChatOpenAI(model='step-5-preview',api_key='test-only',base_url='https://aigw.saurlax.com/v1',http_async_client=client,streaming=False,max_retries=0)
            with budget_scope(tmp_path,'study'):
                with pytest.raises(ResearchBudgetExhausted):await model.ainvoke('short')
        assert runtime.status()['used']==2 and len(runtime.requests())==2
    asyncio.run(work())


def test_formal_handoff_protects_frozen_metrics_and_rejects_new_fit(tmp_path,monkeypatch):
    # Reuse the real-data/real-kernel fixture, while model code replies stay deterministic.
    from test_supervised_comparison import setup as fixture
    from test_supervised_comparison import observe_comparison
    from ai_system.modules.experiments.handoff import handoff,proof_issues
    from ai_system.modules.experiments.execution import build_tools
    from langchain_core.tools import StructuredTool
    setup=fixture.__wrapped__(tmp_path,monkeypatch)
    async def work():
        try:
            exploration=await observe_comparison(setup.ctx,setup.branch);runs=[]
            text=await handoff(setup.ctx,setup.branch,exploration,runs)
            assert 'comparison_estimator' in text and setup.ctx.comparison_proof['protected']
            assert proof_issues(setup.ctx,{'requirement_evidence':[]})
            protected=list(setup.ctx.comparison_proof['protected'])
            assert not proof_issues(setup.ctx,{'requirement_evidence':[{'evidence_refs':protected}]})
            tools=build_tools(setup.ctx,'Q1',runs);tool=next(t for t in tools if t.name=='run_python')
            before=len(runs);reply=await tool.ainvoke({'code':'comparison_estimator.fit(_X, _data["y_train"])'})
            assert '禁止' in reply and len(runs)==before
        finally:await setup.ctx.kernels.close()
    asyncio.run(work())


def test_compact_plan_merges_prose_without_removing_validation_sections(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from test_native_latex import accepted_store
    from test_research_p2 import problem,TASK
    from ai_system.modules.writing import sections as writing
    store=accepted_store(tmp_path);ctx=GraphContext.create(str(tmp_path),planner_llm=None,compact_paper=True);ctx.research=store
    state={'analysis':problem().model_dump(),'task':TASK,'designs':{},'solutions':{}}
    calls=[]
    async def generate(ctx,state,items):
        calls.extend(key for key,_ in items)
        return {key:'已保存研究边界、公式、登记结果与局限。'*10 for key,_ in items}
    monkeypatch.setattr(writing,'write_many',generate)
    updated=asyncio.run(writing.write(state,SimpleNamespace(context=ctx)))
    assert {'abstract','evaluation','references'}<=set(updated['sections'])
    assert {key for key in updated['sections'] if key.startswith('solve:')}=={'solve:Q1','solve:Q2','solve:Q3'}
    assert len(calls)==6  # Three actual study tasks + abstract/evaluation + deterministic bibliography.
    assert len(writing.COMPACT_OUTLINE)==3
