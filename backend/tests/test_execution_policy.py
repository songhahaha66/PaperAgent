"""Real SDK + controlled HTTP responses: never calls a model service."""
import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx,pytest
from ai_system.research.runtime import ResearchRuntime,budget_scope,ResearchBudgetExhausted,RepeatedOperationBlocked,ModelServiceError,async_request_hook,async_response_hook
from ai_system.core_handlers.llm_providers import MeteredChatOpenAI


def completion():
    return {'id':'controlled','object':'chat.completion','created':0,'model':'controlled',
            'choices':[{'index':0,'message':{'role':'assistant','content':'complete'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':7,'completion_tokens':3,'total_tokens':10}}


def client_model(handler,**kwargs):
    client=httpx.AsyncClient(transport=httpx.MockTransport(handler),event_hooks={'request':[async_request_hook],'response':[async_response_hook]})
    model=MeteredChatOpenAI(model='controlled',api_key='fixture-not-a-key',base_url='https://model.invalid/v1',http_async_client=client,streaming=False,max_retries=6,timeout=600,**kwargs)
    return client,model


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    # Retry scheduling is still exercised, without wall-clock delays.
    import ai_system.core_handlers.model_runtime as layer
    monkeypatch.setattr(layer,'RETRY_DELAYS',(0,0))


def test_more_than_40_and_usage_survive_resume(tmp_path):
    sent=[]
    def handle(req):sent.append(req);return httpx.Response(200,json=completion())
    async def run():
        client,model=client_model(handle)
        async with client:
            for cycle in range(2):
                with budget_scope(tmp_path,f'study-{cycle}'):
                    for _ in range(25):assert (await model.ainvoke('normal work')).content=='complete'
    asyncio.run(run())
    state=ResearchRuntime(tmp_path).status()
    assert state['request_limit'] is None and state['limit_source']=='default'
    assert state['used']==50 and len(sent)==50
    assert state['input_tokens']==350 and state['output_tokens']==150 and state['usage_reported_calls']==50
    assert all(req.extensions['timeout']['read']==600 for req in sent)


def test_explicit_cap_checked_before_next_send_and_old_limit_retained(tmp_path):
    runtime=ResearchRuntime(tmp_path);runtime.configure({'request_limit':2,'compact_paper':True,'confirm_models':True})
    for _ in range(2):runtime.reserve('model.invalid')
    with pytest.raises(ResearchBudgetExhausted):ResearchRuntime(tmp_path,'resume').reserve('model.invalid')
    assert runtime.status()['used']==2 and runtime.stop_reason()['kind']=='explicit_limit'
    # Seed the exact pre-versioning shape and verify no silent unlimited migration.
    with runtime.store._transaction() as db:
        db.execute('UPDATE runtime_policy SET payload=?',(json.dumps({'request_limit':40,'timeout_seconds':90,'confirm_models':True,'compact_paper':True}),))
        db.execute('UPDATE request_budgets SET payload=?',(json.dumps({'request_limit':40,'timeout_seconds':90,'used':40,'state':'budget_exhausted'}),))
    restored=ResearchRuntime(tmp_path)
    assert restored.policy()['limit_source']=='legacy' and restored.status()['request_limit']==40
    with pytest.raises(ResearchBudgetExhausted):restored.reserve('model.invalid')
    assert restored.status()['used']==40


def test_absent_policy_in_old_project_is_legacy_not_new_default(tmp_path):
    (tmp_path/'.system').mkdir();(tmp_path/'.system/graph_thread.json').write_text('{"thread_id":"old"}')
    runtime=ResearchRuntime(tmp_path)
    assert runtime.policy()['request_limit']==40 and runtime.policy()['limit_source']=='legacy'


@pytest.mark.parametrize('status,body,expected',[(429,{'error':{'code':'rate_limit_exceeded'}},3),(401,{'error':{'code':'invalid_api_key'}},1),(429,{'error':{'code':'insufficient_quota'}},1),(503,{},3)])
def test_transient_and_terminal_failures_have_one_retry_owner_and_durable_guard(tmp_path,status,body,expected):
    sent=[]
    def handle(req):sent.append(req);return httpx.Response(status,json=body)
    async def run():
        client,model=client_model(handle)
        async with client:
            with budget_scope(tmp_path,'study'):
                with pytest.raises(ModelServiceError):await model.ainvoke('same operation')
            with budget_scope(tmp_path,'resume'):
                with pytest.raises(RepeatedOperationBlocked):await model.ainvoke('same operation')
            if expected==3:
                # A genuine changed input is a separate operation, not a reset of usage.
                with budget_scope(tmp_path,'resume'):
                    with pytest.raises(ModelServiceError):await model.ainvoke('changed input')
    asyncio.run(run())
    assert len(sent)==expected*(2 if expected==3 else 1)
    assert ResearchRuntime(tmp_path).status()['used']==len(sent)


def test_temporary_failure_then_success_and_other_operation_are_not_blocked(tmp_path):
    sent=[]
    def handle(req):
        sent.append(req)
        return httpx.Response(429,json={'error':{'code':'rate_limit_exceeded'}}) if len(sent)<3 else httpx.Response(200,json=completion())
    async def run():
        client,model=client_model(handle)
        async with client,budget_scope_async(tmp_path):
            assert (await model.ainvoke('retry')).content=='complete'
            assert (await model.ainvoke('unrelated completed task')).content=='complete'
    asyncio.run(run());assert len(sent)==4


class budget_scope_async:
    def __init__(self,path):self.scope=budget_scope(path)
    async def __aenter__(self):return self.scope.__enter__()
    async def __aexit__(self,*args):return self.scope.__exit__(*args)


def test_waiting_and_manual_pause_projections_do_not_start_or_reset(tmp_path):
    from ai_system.research.runtime import execution_reason
    runtime=ResearchRuntime(tmp_path);runtime.reserve('model.invalid');runtime.note_stop('manual_pause','已手动暂停')
    assert execution_reason(tmp_path,'paused')['kind']=='manual_pause'
    assert execution_reason(tmp_path,'waiting')['kind']=='waiting_confirmation'
    assert execution_reason(tmp_path,'completed') is None
    assert runtime.status()['used']==1


def test_partial_stream_is_never_automatically_replayed(tmp_path):
    sent=[]
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"id":"partial","object":"chat.completion.chunk","created":0,"model":"controlled","choices":[{"index":0,"delta":{"role":"assistant","content":"already emitted"},"finish_reason":null}]}\n\n'
            raise httpx.ReadError('controlled stream interruption')
    def handle(req):sent.append(req);return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=BrokenStream())
    async def run():
        client,model=client_model(handle);model=model.model_copy(update={'streaming':True});chunks=[]
        async with client:
            with budget_scope(tmp_path):
                with pytest.raises(ModelServiceError) as caught:
                    async for chunk in model.astream('stream operation'):chunks.append(chunk.content)
        assert 'already emitted' in ''.join(chunks),repr(caught.value.__cause__)
    asyncio.run(run())
    assert len(sent)==1 and ResearchRuntime(tmp_path).status()['used']==1


def test_repeated_error_cannot_evade_guard_by_alternating_with_another_error(tmp_path):
    runtime=ResearchRuntime(tmp_path)
    runtime.failed('operation','same-error','invalid_response','saved')
    runtime.failed('operation','another-error','invalid_response','saved')
    runtime.failed('operation','same-error','invalid_response','saved')
    runtime.failed('operation','same-error','invalid_response','saved')
    with pytest.raises(RepeatedOperationBlocked):ResearchRuntime(tmp_path).check_guard('operation')
    runtime.check_guard('genuinely-new-operation')
    assert runtime.stop_reason()['kind']=='repeated_failure'


def test_known_legacy_token_usage_is_imported_once_before_new_requests(tmp_path):
    rt=ResearchRuntime(tmp_path);rt.reserve('legacy')
    with rt.store._transaction() as db:
        db.execute('UPDATE request_budgets SET payload=?',(json.dumps({'used':1,'request_limit':40,'timeout_seconds':90,'state':'active'}),))
    (tmp_path/'.system/llm_calls.json').write_text(json.dumps({'calls':[{'input':11,'output':5,'usage_returned':True},{'input':0,'output':0,'usage_returned':False}]}))
    assert rt.status()['input_tokens']==11
    seq=rt.reserve('new');rt.usage(seq,{'input_tokens':7,'output_tokens':3})
    assert ResearchRuntime(tmp_path).status()['input_tokens']==18
    assert rt.status()['output_tokens']==8
    rt.usage(seq,{'input_tokens':7,'output_tokens':3})
    assert rt.status()['input_tokens']==18


def test_new_default_does_not_override_explicit_confirmation_environment(tmp_path,monkeypatch):
    from ai_system.graph.agent import PaperGraphAgent
    monkeypatch.setenv('AI_CONFIRM_MODELS','true')
    agent=PaperGraphAgent(None,workspace_dir=str(tmp_path),judge=None)
    assert agent.options['confirm_models'] is True
    ResearchRuntime(tmp_path).configure({'confirm_models':False})
    agent=PaperGraphAgent(None,workspace_dir=str(tmp_path),judge=None)
    assert agent.options['confirm_models'] is False


def test_resume_cannot_bypass_pending_confirmation():
    from services.chat_services.research_session import ResearchSnapshot,resolve_action
    snapshot=ResearchSnapshot(work_id='pending',work_status='waiting',research_id='study',engine_version='research-v3',graph_status='interrupted')
    with pytest.raises(ValueError,match='等待方案确认'):resolve_action('resume','继续',snapshot)
    assert resolve_action('confirm','确认方案',snapshot)=='confirm'


def test_pre_policy_research_traces_retain_legacy_limit(tmp_path):
    (tmp_path/'.system').mkdir();(tmp_path/'.system/llm_calls.json').write_text('{"calls":[{"input":3,"output":2,"usage_returned":true}]}')
    assert ResearchRuntime(tmp_path).policy()['request_limit']==40
