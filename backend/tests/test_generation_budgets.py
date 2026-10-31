"""Actual client serialization, stream termination, concurrent accounting and durable wrap-up."""
import asyncio,json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import httpx,pytest
from langchain_core.messages import HumanMessage,AIMessage
from ai_system.config.generation import GenerationSettings,ModelGenerationLimit,request_model
from ai_system.core_handlers.llm_providers import MeteredChatOpenAI,create_llm_from_model_config
from ai_system.research.runtime import ResearchRuntime,budget_scope,ResearchBudgetExhausted,BudgetUnavailable
from ai_system.research.store import ResearchStore
from helpers.model_response import response


def client(handler,streaming=False):
    return MeteredChatOpenAI(model='unknown-gateway-model',api_key='offline',base_url='https://offline.invalid/v1',
        max_tokens=16000,streaming=streaming,http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_explicit_connection_policy_reaches_gateway_without_thinking_heuristic():
    llm=create_llm_from_model_config(SimpleNamespace(model_id='gateway-alias',base_url='https://offline.invalid/v1',api_key='offline',
        generation_settings={'max_output_tokens':24000,'context_window':64000,'reasoning_effort':'low'}))
    payload=request_model(llm)._get_request_payload([HumanMessage('Calculate')])
    assert payload['max_completion_tokens']==24000 and payload['reasoning_effort']=='low'
    assert llm.profile['max_input_tokens']==40000 and llm.extra_body is None
    assert request_model(llm,'summary').max_tokens==1800 and llm.max_tokens==24000
    step=create_llm_from_model_config(SimpleNamespace(model_id='step-5-preview',base_url='https://aigw.saurlax.com/v1',api_key='offline',generation_settings={'reasoning_effort':'high'}))
    assert step.reasoning_effort=='high' and step.extra_body['reasoning_effort']=='high'
    with pytest.raises(ValueError):GenerationSettings(max_output_tokens=16000,context_window=16000)


def test_atomic_reservations_and_unknown_usage_share_one_workspace_budget(tmp_path):
    root=tmp_path.resolve();meter=ResearchRuntime(root,'main');meter.configure({'output_token_limit':120})
    def reserve(name):return ResearchRuntime(root,name).reserve(name,input_tokens=10,output_tokens=90)
    with ThreadPoolExecutor(max_workers=2) as pool:seq=list(pool.map(reserve,['main','child']))
    limits={i:meter.generation_limit(i) for i in seq}
    assert sorted(limits.values())==[30,90]
    first=next(i for i in seq if limits[i]==90);unknown=next(i for i in seq if limits[i]==30)
    meter.usage(first,{'input_tokens':10,'output_tokens':50})
    meter.usage(unknown,state='failed')
    third=meter.reserve('summary',input_tokens=1,output_tokens=90)
    assert meter.generation_limit(third)==40
    meter.usage(third,{'input_tokens':1,'output_tokens':40})
    with pytest.raises(ResearchBudgetExhausted):meter.reserve('again',output_tokens=1)
    state=meter.status()
    assert state['output_tokens']==90 and state['reserved_output_tokens']==30 and state['unmeasured_requests']==1
    assert len(meter.requests())==3


def test_cost_requires_prices_and_reconciles_only_known_usage(tmp_path):
    meter=ResearchRuntime(tmp_path);meter.configure({'cost_limit_usd':.001})
    with pytest.raises(BudgetUnavailable):meter.reserve('unpriced',input_tokens=100,output_tokens=100)
    assert not meter.requests()
    price={'input_per_million':1.,'output_per_million':2.}
    seq=meter.reserve('priced',input_tokens=100,output_tokens=10000,prices=price)
    assert meter.generation_limit(seq)==450
    meter.usage(seq,{'input_tokens':50,'output_tokens':20})
    assert meter.status()['cost_usd']==pytest.approx(.00009)
    assert meter.status()['reserved_cost_usd']==0


@pytest.mark.parametrize('streaming',[False,True])
def test_real_client_truncation_never_retries_and_accounts_reasoning(tmp_path,streaming):
    calls=[]
    def handle(request):
        calls.append(json.loads(request.content))
        raw=response('length',16000)
        if not streaming:return httpx.Response(200,json=raw)
        chunks=[{**{k:v for k,v in raw.items() if k not in {'choices','usage'}},'choices':[{'index':0,'delta':{'role':'assistant','content':''},'finish_reason':'length'}]},
                {**{k:v for k,v in raw.items() if k!='choices'},'choices':[]}]
        return httpx.Response(200,headers={'content-type':'text/event-stream'},text=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n')
    llm=client(handle,streaming);meter=ResearchRuntime(tmp_path)
    async def run():
        with budget_scope(tmp_path):
            with pytest.raises(ModelGenerationLimit):await llm.ainvoke([HumanMessage('work')])
    asyncio.run(run())
    assert len(calls)==1
    rows=meter.requests()
    assert len(rows)==1 and rows[0]['state']=='truncated'
    assert rows[0]['reasoning_tokens']==15999 and rows[0]['diagnostic']['finish_reason']=='length'


def test_runtime_generation_allocation_is_used_in_actual_wire_request(tmp_path):
    calls=[]
    def handle(request):calls.append(json.loads(request.content));return httpx.Response(200,json=response())
    llm=client(handle);meter=ResearchRuntime(tmp_path);meter.configure({'output_token_limit':500})
    async def run():
        with budget_scope(tmp_path):await llm.ainvoke([HumanMessage('work')])
    asyncio.run(run())
    assert calls[0]['max_completion_tokens']==500 and llm.max_tokens==16000
    assert meter.status()['reserved_output_tokens']==0 and meter.status()['output_tokens']==10


def test_truncated_valid_tool_call_never_executes_in_real_graph(tmp_path):
    from ai_system.graph.agent import PaperGraphAgent
    calls=[]
    def handle(request):
        calls.append(1);raw=response('length',16000)
        raw['choices'][0]['message']={'role':'assistant','content':'','tool_calls':[{'id':'partial', 'type':'function',
            'function':{'name':'research_run_python','arguments':json.dumps({'code':"print('must not run')"})}}]}
        return httpx.Response(200,json=raw)
    agent=PaperGraphAgent(client(handle),workspace_dir=str(tmp_path))
    asyncio.run(agent.run('计算并保存一份结果。'))
    state=ResearchStore(tmp_path,agent._load_thread()).snapshot()
    from services.file_services.workspace_fs import WorkspaceFS
    assert not WorkspaceFS(str(tmp_path))._manifest.get('runs')
    assert len(calls)==1 and state['action']['error_type']=='ModelGenerationLimit'
    assert '截断' in state['action']['message'] and state['outcome']!='completed'


@pytest.mark.parametrize('request_limit,prepared',[(10,8),(6,4)])
def test_original_agent_receives_wrapup_once_as_preserved_message(tmp_path,request_limit,prepared):
    from ai_system.graph.context import GraphContext
    from ai_system.research.loop import prepare_action
    from research_tools_helpers import ResearchToolsModel,tool
    seen=[];meter=ResearchRuntime(tmp_path);meter.configure({'request_limit':request_limit})
    for _ in range(prepared):
        seq=meter.reserve('preparation');meter.usage(seq,{'input_tokens':1,'output_tokens':1})
    def respond(messages):
        seen.append(messages);seq=meter.reserve('controlled');meter.usage(seq,{'input_tokens':1,'output_tokens':1})
        return tool('research_list_files',{},'list') if len(seen)==1 else tool('research_pause',{'message':'保留现有成果，剩余问题待继续。'},'pause')
    ctx=GraphContext.create(str(tmp_path),planner_llm=ResearchToolsModel(respond=respond));ctx.research=ResearchStore(tmp_path,'wrapup')
    ctx.research.initialize('读取材料并完成分析。')
    async def run():
        with budget_scope(tmp_path,'wrapup'):await prepare_action(ctx)
    asyncio.run(run())
    assert len(seen)==2
    notes=[[m for m in rows if (m.id or '').startswith('budget-wrapup:')] for rows in seen]
    assert all(len(n)==1 for n in notes) and notes[0][0].id==notes[1][0].id
    assert meter.status()['used']==request_limit


def test_generation_migration_keeps_existing_connection_and_defaults(tmp_path):
    import importlib.util
    from sqlalchemy import create_engine,text
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path
    path=Path(__file__).parents[1]/'alembic/versions/d31a70f92b46_model_generation_settings.py'
    spec=importlib.util.spec_from_file_location('generation_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with create_engine('sqlite://').begin() as db:
        db.execute(text('CREATE TABLE model_configs(id INTEGER PRIMARY KEY,model_id TEXT)'))
        db.execute(text("INSERT INTO model_configs VALUES (1,'existing-alias')"))
        module.op=Operations(MigrationContext.configure(db));module.upgrade()
        row=db.execute(text('SELECT model_id,generation_settings FROM model_configs')).one()
        assert row[0]=='existing-alias' and json.loads(row[1])=={}
