"""Real SDK + controlled HTTP responses: never calls a model service."""
import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx,pytest
from ai_system.research.runtime import ResearchRuntime,budget_scope,ResearchBudgetExhausted,RepeatedOperationBlocked,ModelServiceError,async_request_hook,async_response_hook
from ai_system.core_handlers.llm_providers import MeteredChatOpenAI


def client_model(handler,**kwargs):
    client=httpx.AsyncClient(transport=httpx.MockTransport(handler),event_hooks={'request':[async_request_hook],'response':[async_response_hook]})
    model=MeteredChatOpenAI(model='controlled',api_key='fixture-not-a-key',base_url='https://model.invalid/v1',http_async_client=client,streaming=False,max_retries=6,timeout=600,**kwargs)
    return client,model


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    # Retry scheduling is still exercised, without wall-clock delays.
    import ai_system.core_handlers.model_runtime as layer
    monkeypatch.setattr(layer,'RETRY_DELAYS',(0,0))


def test_explicit_cap_checked_before_next_send_and_old_limit_retained(tmp_path):
    runtime=ResearchRuntime(tmp_path);runtime.configure({'request_limit':2,'compact_paper':True,'confirm_models':True})
    for _ in range(2):runtime.reserve('model.invalid')
    with pytest.raises(ResearchBudgetExhausted):ResearchRuntime(tmp_path,'resume').reserve('model.invalid')
    assert runtime.status()['used']==2 and runtime.stop_reason()['kind']=='explicit_limit'
    # Seed the exact pre-versioning shape and verify no silent unlimited migration.
    with runtime.store._transaction() as db:
        db.execute('UPDATE runtime_policy SET payload=?',(json.dumps({'request_limit':40,'timeout_seconds':90,'confirm_models':True,'compact_paper':True}),))
        db.execute('UPDATE request_budgets SET payload=?',(json.dumps({'request_limit':40,'timeout_seconds':90,'used':40,'state':'budget_exhausted'}),))
    with runtime.store._transaction() as db:db.execute('DELETE FROM domain_migrations')
    restored=ResearchRuntime(tmp_path)
    assert restored.policy()['limit_source']=='legacy' and restored.status()['request_limit']==40
    with pytest.raises(ResearchBudgetExhausted):restored.reserve('model.invalid')
    assert restored.status()['used']==40


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


@pytest.mark.parametrize('streaming',[False,True])
def test_connection_failure_retains_safe_causes_and_configured_timeout(tmp_path,streaming):
    import socket
    sent=[]
    def handle(req):
        sent.append(req)
        # A supplied httpx client defaults to 5s; the SDK must still apply the
        # configured model timeout to the actual request.
        assert req.extensions['timeout']==dict.fromkeys(('connect','read','write','pool'),600)
        try:raise socket.gaierror('sensitive request detail must not be saved')
        except socket.gaierror as cause:
            raise httpx.ConnectError('fixture-secret-key',request=req) from cause
    async def run():
        client,model=client_model(handle)
        async with client:
            with budget_scope(tmp_path):
                with pytest.raises(ModelServiceError,match='检查网络'):
                    if streaming:
                        async for _ in model.astream('connection test'):pass
                    else:await model.ainvoke('connection test')
    asyncio.run(run())
    rows=ResearchRuntime(tmp_path).requests()
    assert len(sent)==len(rows)==3
    for row in rows:
        assert row['diagnostic']=={'error_type':'APIConnectionError',
            'error_chain':['APIConnectionError','ConnectError','gaierror'],'error_kind':'connection'}
        assert 'fixture-secret-key' not in json.dumps(row)
        assert 'sensitive request detail' not in json.dumps(row)


def test_repeated_error_cannot_evade_guard_by_alternating_with_another_error(tmp_path):
    runtime=ResearchRuntime(tmp_path)
    runtime.failed('operation','same-error','invalid_response','saved')
    runtime.failed('operation','another-error','invalid_response','saved')
    runtime.failed('operation','same-error','invalid_response','saved')
    runtime.failed('operation','same-error','invalid_response','saved')
    with pytest.raises(RepeatedOperationBlocked):ResearchRuntime(tmp_path).check_guard('operation')
    runtime.check_guard('genuinely-new-operation')
    assert runtime.stop_reason()['kind']=='repeated_failure'


def test_resume_cannot_bypass_pending_confirmation():
    from services.chat_services.research_session import ResearchSnapshot,resolve_action
    snapshot=ResearchSnapshot(work_id='pending',work_status='waiting',research_id='study',engine_version='research-v3',graph_status='interrupted')
    with pytest.raises(ValueError, match='等待必要信息'):
        asyncio.run(resolve_action('resume','继续',snapshot))
    assert asyncio.run(resolve_action('confirm','确认方案',snapshot))=='confirm'
