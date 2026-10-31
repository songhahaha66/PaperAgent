"""Thread, lifecycle and multimodal boundary regressions without network calls."""
import asyncio
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from langchain_core.messages import ToolMessage,HumanMessage,AIMessage
from ai_system.graph.context import KernelPool,GraphContext
from ai_system.graph.images import ImageProjection,page_ref,vision_policy
from ai_system.graph.compaction import ContextMiddleware,token_estimate
from ai_system.research.store import ResearchStore
from services.file_services.workspace_layout import workspace_path


def page(root,number=1):
    from PIL import Image
    name=f'.system/paper-project/previews/{"a"*64}/{number}.png'
    path=workspace_path(root,name);path.parent.mkdir(parents=True,exist_ok=True)
    Image.new('RGB',(1024,1024),'white').save(path)
    return page_ref(root,{'path':name,'page':number})


def receipt(ref,identity):
    return ToolMessage(content=json.dumps({'source_version':'b'*64,'pdf_sha256':'a'*64,
        '_images':[ref],'pages':[{'page':ref['page'],'text':'actual page'}]}),tool_call_id=identity,id=identity)


def deepseek(**settings):
    return SimpleNamespace(model_name='deepseek-flash',openai_api_base='https://api.deepseek.com/v1',
        metadata={'generation_settings':settings},profile={})


@pytest.mark.parametrize('name,strategy,tokens',[
    ('gpt-4o','openai-tiles',765),('gpt-4o-mini','openai-mini-tiles',25501),
    ('gpt-6-astra','openai-patches',1229),('gemini-3.8-flash','gemini',1120),
    ('claude-opus-4-7','claude',1369)])
def test_images_follow_model_rules(name,strategy,tokens):
    policy=vision_policy(SimpleNamespace(model_name=name,profile={}))
    assert policy.enabled and policy.strategy==strategy and policy.tokens(1024,1024)==tokens


def test_deepseek_native_vision_and_explicit_gateway_configuration():
    from ai_system.config.generation import GenerationSettings,ModelContextLimit
    assert vision_policy(deepseek()).enabled
    assert vision_policy(deepseek()).tokens(1600,1600)==1024
    stale=deepseek();stale.profile={'image_inputs':False}
    assert vision_policy(stale).enabled, 'official route metadata wins over an outdated SDK catalog'
    assert not vision_policy(deepseek(image_inputs=False)).enabled
    custom=SimpleNamespace(model_name='custom',profile={},metadata={'generation_settings':
        GenerationSettings(image_inputs=True,image_tokens_per_image=4000).model_dump()})
    assert vision_policy(custom).tokens(1024,1024)==4000
    with pytest.raises(ModelContextLimit):vision_policy(SimpleNamespace(profile={'image_inputs':True})).tokens(100,100)


def test_old_images_are_durably_omitted_and_can_be_read_again(tmp_path):
    first=receipt(page(tmp_path,1),'one');second=receipt(page(tmp_path,2),'two')
    omitted={};media=ImageProjection(tmp_path,deepseek(),omitted)
    view=media.view([first,second],max_tokens=1024)
    assert omitted and all(isinstance(m,ToolMessage) for m in view[:2])
    assert isinstance(view[-1],HumanMessage), 'DeepSeek receives documented user-image blocks after tool responses'
    assert '"visual_pages": []' in view[0].content
    assert 'image_ref' in str(view[-1].content) and 'base64' not in str(view)
    wire=media.hydrate(view)
    assert wire[-1].content[-1]['image_url']['url'].startswith('data:image/png;base64,')
    assert 'base64' not in first.content and 'base64' not in second.content
    restored=ImageProjection(tmp_path,deepseek(),json.loads(json.dumps(omitted)))
    again=restored.view([first,second],max_tokens=10000)
    assert '"visual_pages": []' in again[0].content
    reread=receipt(page(tmp_path,1),'new-read')
    fresh=restored.view([first,second,reread],max_tokens=10000)
    assert '"visual_pages": [1]' in fresh[2].content


def test_inline_byte_limit_and_hash_verification(tmp_path):
    from dataclasses import replace
    ref=page(tmp_path);message=receipt(ref,'one');media=ImageProjection(tmp_path,deepseek(),{})
    media.policy=replace(media.policy,max_inline_bytes=1)
    assert all(not isinstance(m.content,list) for m in media.view([message],max_tokens=10000))
    media=ImageProjection(tmp_path,deepseek(),{})
    view=media.view([message],max_tokens=10000)
    workspace_path(tmp_path,ref['path']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='校验失败'):media.hydrate(view)


def test_native_image_meter_prices_visual_content_instead_of_base64_text(tmp_path):
    from ai_system.core_handlers.model_runtime import reservation
    message=receipt(page(tmp_path),'one')
    media=ImageProjection(tmp_path,deepseek(),{})
    block=media.hydrate(media.view([message],max_tokens=10000))[-1].content[-1]
    assert token_estimate(block,image_counter=media.native_tokens)==1024
    mini=ImageProjection(tmp_path,SimpleNamespace(model_name='gpt-4o-mini',profile={}),{})
    assert token_estimate(block,image_counter=mini.native_tokens)==25501
    wire=HumanMessage(content=[block])
    native_reservation=reservation(deepseek(),([wire],),{})['input_tokens']
    mini_reservation=reservation(SimpleNamespace(model_name='gpt-4o-mini',profile={}),([wire],),{})['input_tokens']
    assert mini_reservation-native_reservation==25501-1024
    assert native_reservation<2000


def test_actual_input_usage_calibrates_same_prefix_but_not_a_different_route(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from langchain.agents.middleware import ModelResponse
    from ai_system.research import runtime
    stats=[]
    @contextmanager
    def scope(**kwargs):
        stats.append(kwargs['context_usage'])
        yield
    monkeypatch.setattr(runtime,'execution_scope',scope)
    model=deepseek();model.profile={'max_input_tokens':100000}
    ctx=GraphContext.create(str(tmp_path),planner_llm=model)
    ctx.research=ResearchStore(tmp_path,'usage');ctx.research.initialize('check image usage')
    middleware=ContextMiddleware(ctx,model,'usage',pinned={})
    class Request:
        tools=[]
        def __init__(self,messages):self.messages=messages
        def override(self,**kwargs):return Request(kwargs['messages'])
    reply=AIMessage(content='checked',usage_metadata={'input_tokens':10000,'output_tokens':1,'total_tokens':10001})
    async def handler(request):return ModelResponse(result=[reply])
    async def run():
        messages=[HumanMessage(content='Check page'),AIMessage(content='',tool_calls=[
            {'name':'writing_preview','args':{},'id':'one'}]),receipt(page(tmp_path),'one')]
        await middleware.awrap_model_call(Request(messages),handler)
        messages += [reply,HumanMessage(content='Now explain it')]
        await middleware.awrap_model_call(Request(messages),handler)
        model.metadata={'generation_settings':{'image_tokens_per_image':2048}}
        await middleware.awrap_model_call(Request(messages),handler)
    asyncio.run(run())
    assert stats[0]['total_estimated_tokens']<10000
    assert stats[1]['total_estimated_tokens']>10000
    assert stats[2]['total_estimated_tokens']<10000, 'model/meter changes invalidate the usage anchor'


def test_pdf_projection_reaches_real_sdk_and_budget_via_mock_http(tmp_path):
    import httpx
    from ai_system.core_handlers.llm_providers import MeteredChatOpenAI
    from ai_system.research.runtime import budget_scope,ResearchRuntime
    sent=[]
    def handle(request):
        body=json.loads(request.content);sent.append(body)
        assert body['messages'][-2]['role']=='tool'
        assert body['messages'][-1]['role']=='user'
        assert body['messages'][-1]['content'][-1]['image_url']['url'].startswith('data:image/png;base64,')
        return httpx.Response(200,json={'id':'offline','object':'chat.completion','created':0,
            'model':'deepseek-flash','choices':[{'index':0,'message':{'role':'assistant','content':'page checked'},
            'finish_reason':'stop'}],'usage':{'prompt_tokens':1500,'completion_tokens':2,'total_tokens':1502}})
    class Request:
        tools=[]
        def __init__(self,messages):self.messages=messages
        def override(self,**kwargs):return Request(kwargs['messages'])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            model=MeteredChatOpenAI(model='deepseek-flash',api_key='offline',
                base_url='https://api.deepseek.com/v1',http_async_client=client,max_tokens=1000,
                profile={'max_input_tokens':100000,'image_inputs':False})
            ctx=GraphContext.create(str(tmp_path),planner_llm=model)
            ctx.research=ResearchStore(tmp_path,'sdk');ctx.research.initialize('check page')
            middleware=ContextMiddleware(ctx,model,'sdk',pinned={})
            messages=[HumanMessage('check'),AIMessage(content='',tool_calls=[
                {'name':'writing_preview','args':{},'id':'one'}]),receipt(page(tmp_path),'one')]
            async def handler(request):return await model.ainvoke(request.messages)
            with budget_scope(tmp_path,'sdk'):
                result=await middleware.awrap_model_call(Request(messages),handler)
            assert result.content=='page checked'
            assert 'base64' not in str(ctx.agent_messages)
    asyncio.run(run())
    rows=ResearchRuntime(tmp_path).requests()
    assert len(sent)==len(rows)==1 and rows[0]['state']=='completed'
    assert 1024<=rows[0]['reservation']['input_tokens']<4000
    assert 'base64' not in json.dumps(rows)


def test_text_offload_preserves_active_images_and_retained_read_handles(tmp_path):
    message=receipt(page(tmp_path),'one')
    value=json.loads(message.content);value['pages'][0]['text']='many words '*8000
    message=message.model_copy(update={'content':json.dumps(value)})
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    middleware=ContextMiddleware(ctx,deepseek(),'images')
    short=middleware.offload(message)
    assert json.loads(short.content)['_images']==value['_images']
    old=middleware.offload(message,old=True)
    data=json.loads(old.content)
    assert '_images' not in data and data['image_refs']==value['_images']
    assert data['source_version']=='b'*64 and data['pdf_sha256']=='a'*64
    assert 'base64' not in old.content


@pytest.mark.parametrize('operation',['list_files','search_files','read_artifact'])
def test_worker_reads_frozen_bindings_while_loop_mutates_manifest(tmp_path,monkeypatch,operation):
    from ai_system.research import files
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    ctx.research=ResearchStore(tmp_path,'frozen');ctx.research.initialize('read existing files')
    ctx.fs.write_text('outputs/a.txt','original text')
    ctx.fs.register_file('outputs/a.txt',visibility='user',source_run='original-run')
    original=files.resolve;entered=threading.Event();release=threading.Event()
    def resolve(view,name):
        assert view.fs is not ctx.fs and view.fs._manifest is not ctx.fs._manifest
        entered.set();assert release.wait(3)
        assert view.fs.get_file_meta(name)['source_run']=='original-run'
        return original(view,name)
    monkeypatch.setattr(files,'resolve',resolve)
    async def run():
        inputs={'path':'outputs/a.txt','start':0,'limit':30} if operation=='read_artifact' else {'pattern':'outputs/*','offset':0,'limit':30,'query':'original'}
        task=asyncio.create_task(getattr(files,operation)(ctx,inputs))
        try:
            assert await asyncio.to_thread(entered.wait,3)
            ctx.fs._manifest['files']['outputs/a.txt']['source_run']='changed-run'
            ctx.fs._manifest['files'].clear()
        finally:release.set()
        result=(await task).value
        if operation=='list_files':assert result['files'][0]['source_run']=='original-run'
        elif operation=='search_files':assert result['matches'][0]['path']=='outputs/a.txt'
        else:assert result['excerpt']=='original text'
    asyncio.run(run())


def test_disconnect_failure_cannot_replace_success_and_failed_kernel_is_retryable(monkeypatch):
    from unittest.mock import Mock
    from ai_system.graph import context
    warning=Mock();monkeypatch.setattr(context.logger,'warning',warning)
    class Kernel:
        failed=True
        async def disconnect(self):
            if self.failed:raise ConnectionError('controlled cleanup failure')
    async def run():
        pool=KernelPool(Path('/unused'),None);bad=Kernel();good=Kernel();good.failed=False
        pool._kernels={'bad':bad,'good':good}
        async def successful_call():
            try:return 'completed'
            finally:await pool.disconnect()
        assert await successful_call()=='completed'
        assert pool._kernels=={'bad':bad}
        bad.failed=False;await pool.disconnect();assert not pool._kernels
    asyncio.run(run())
    warning.assert_called_once()
    assert '保留对象' in warning.call_args.args[0]


def test_outer_cancellation_is_not_swallowed_by_disconnect():
    async def run():
        entered=asyncio.Event()
        class Kernel:
            async def disconnect(self):entered.set();await asyncio.Event().wait()
        pool=KernelPool(Path('/unused'),None);kernel=Kernel();pool._kernels={'one':kernel}
        task=asyncio.create_task(pool.disconnect());await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert pool._kernels=={'one':kernel}
    asyncio.run(run())


def test_remote_cleanup_attempts_both_clients_even_if_one_fails():
    from unittest.mock import AsyncMock
    from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
    session=OpenSandboxSession.__new__(OpenSandboxSession)
    session.interpreter=SimpleNamespace(aclose=AsyncMock(side_effect=ConnectionError('interpreter cleanup')))
    session.sandbox=SimpleNamespace(close=AsyncMock())
    with pytest.raises(ConnectionError):asyncio.run(session.disconnect())
    session.interpreter.aclose.assert_awaited_once()
    session.sandbox.close.assert_awaited_once()


def test_client_cleanup_failure_retains_remote_identity_and_ready_state(tmp_path):
    from unittest.mock import AsyncMock
    from ai_system.sandbox.runtime_manager import RuntimeManager
    session=SimpleNamespace(cache_key='cleanup',disconnect=AsyncMock(side_effect=ConnectionError('cleanup')))
    settings=SimpleNamespace(provider='opensandbox',endpoint='http://offline',image='offline',cpu=1,memory='1Gi')
    manager=RuntimeManager(tmp_path,session,settings)
    manager.save(state='ready',sandbox_id='retained-container',context_id='retained-context')
    with pytest.raises(ConnectionError):asyncio.run(manager.disconnect())
    retained=json.loads(manager.path.read_text())
    assert retained['state']=='ready' and retained['client_cleanup_error']=='ConnectionError'
    assert retained['sandbox_id']=='retained-container' and retained['context_id']=='retained-context'
    session.disconnect.side_effect=None;asyncio.run(manager.disconnect())
    assert manager.handle['state']=='detached' and manager.handle['client_cleanup_error'] is None
