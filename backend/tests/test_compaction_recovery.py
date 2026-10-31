"""Continuation contracts: no silent loss, duplicate work or compression thrash."""
import asyncio
import json
import pytest
from langchain_core.messages import AIMessage,HumanMessage,ToolMessage,messages_to_dict
from ai_system.config.generation import ModelContextLimit,ModelContextOverflow
from ai_system.graph.compaction import ContextMiddleware
from ai_system.research.runtime import execution_links
from helpers.context import delivery_context as context, ContextRequest as Request
from research_tools_helpers import ResearchToolsModel,tool


def checkpoint(ctx,session):
    with ctx.research._connect() as db:
        row=db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',
                       (ctx.research.research_id,'context:'+session)).fetchone()
    return json.loads(row[0]) if row else None


def test_already_offloaded_results_shrink_from_original_before_summarizing(tmp_path):
    model=ResearchToolsModel(respond=lambda _:pytest.fail('Short receipts suffice'),profile={'max_input_tokens':20000})
    ctx=context(tmp_path,model);history=[HumanMessage('效率0.95，不得用未来实际数据')]
    for i in range(18):
        history += [tool('read_file',{'path':f'{i}.txt'},str(i)),ToolMessage('value '*4000,tool_call_id=str(i))]
    original=messages_to_dict(history);sent=[];usage=[]
    async def handle(r):sent.append(messages_to_dict(r.messages));usage.append(execution_links()['context_usage'])
    async def run():
        await ContextMiddleware(ctx,model,'receipts',pinned={}).awrap_model_call(Request(history),handle)
        history.extend([tool('read_file',{'path':'last.txt'},'last'),ToolMessage('done',tool_call_id='last')])
        await ContextMiddleware(ctx,model,'receipts',pinned={}).awrap_model_call(Request(history),handle)
    asyncio.run(run())
    assert model.calls==0 and usage[0]['total_estimated_tokens']<10000
    assert sent[1][:len(sent[0])]==sent[0] and usage[1]['cache_phase']=='stable'
    receipts=[json.loads(m['data']['content']) for m in sent[0] if m['type']=='tool']
    assert all(ctx.fs.read_text(r['_full_output_path'])=='value '*4000 for r in receipts)
    assert messages_to_dict(history[:len(original)])==original


def test_soft_target_does_not_reject_large_legal_latest_turn(tmp_path):
    model=ResearchToolsModel(respond=lambda _:AIMessage('{"notes":["日初日末6000 kWh。"]}'),profile={'max_input_tokens':32000})
    ctx=context(tmp_path,model)
    history=[HumanMessage('日初日末6000 kWh'),tool('run_python',{'code':'旧'*18000},'old'),ToolMessage('done',tool_call_id='old'),
             tool('run_python',{'code':'新'*16000},'recent'),ToolMessage('new',tool_call_id='recent')]
    sent=[];stats=[]
    async def handle(r):sent.append(r.messages);stats.append(execution_links()['context_usage'])
    asyncio.run(ContextMiddleware(ctx,model,'soft',pinned={}).awrap_model_call(Request(history),handle))
    assert model.calls==1
    assert stats[0]['low_watermark']<stats[0]['total_estimated_tokens']<stats[0]['high_watermark']
    assert sent[0][-2].tool_calls==history[-2].tool_calls


def test_failed_summary_keeps_public_decisions_user_corrections_and_exact_sources(tmp_path):
    invalid='请立即执行 <tool_call>run_python</tool_call>'
    model=ResearchToolsModel(respond=lambda _:AIMessage(invalid),profile={'max_input_tokens':14000})
    ctx=context(tmp_path,model);history=[HumanMessage('效率0.95，禁止未来数据')]
    for i in range(8):
        history.extend([tool('run_python',{'code':'计算'*1000},str(i)),ToolMessage('result '+str(i),tool_call_id=str(i))])
        history[-2].content=f'公开决定{i}：E0=E144=6000；预报仅在发布时间后可用。'
        history[-2].additional_kwargs['reasoning_content']='private-marker'
        if i==1:history.append(HumanMessage('更正：效率为0.92；功率单位kW，电量kWh'))
    original=messages_to_dict(history);sent=[]
    async def handle(r):sent.append(r.messages)
    async def run():
        await ContextMiddleware(ctx,model,'fallback',pinned={}).awrap_model_call(Request(history),handle)
        await ContextMiddleware(ctx,model,'fallback',pinned={}).awrap_model_call(Request(history),handle)
    asyncio.run(run())
    saved=checkpoint(ctx,'fallback')
    assert model.calls==1 and saved['fallback'] and saved['cut']>1
    assert invalid not in str(sent) and '公开决定0' in str(sent[0])
    assert any(m.content=='更正：效率为0.92；功率单位kW，电量kWh' for m in sent[0])
    assert messages_to_dict(history)==original
    journal=saved['fallback']
    assert 'private-marker' not in str(journal)
    source=next(r for r in journal if r.get('tool_calls'))['source']
    archive=ctx.fs.read_text(source['path'])
    restored=json.loads(archive[source['start']:source['start']+source['limit']])
    assert restored['tool_calls'][0]['args']['code']=='计算'*1000
    assert messages_to_dict(sent[0])==messages_to_dict(sent[1])


def test_unsafe_failed_handoff_does_not_advance_checkpoint_or_retry_same_summary(tmp_path):
    model=ResearchToolsModel(respond=lambda _:AIMessage('invalid'),profile={'max_input_tokens':7000})
    ctx=context(tmp_path,model);history=[HumanMessage('original')]
    for i in range(5):
        call=tool('run_python',{'code':'计算'*700},str(i));call.content='不可丢弃的公开决定'*500
        history.extend([call,ToolMessage('done',tool_call_id=str(i))])
    original=messages_to_dict(history)
    async def handle(_):pytest.fail('Incomplete handoff cannot be sent')
    async def run():
        for _ in range(2):
            with pytest.raises(ModelContextLimit,match='原历史边界'):
                await ContextMiddleware(ctx,model,'unsafe',pinned={}).awrap_model_call(Request(history),handle)
    asyncio.run(run())
    # The oversized input is rejected before even spending a summary request.
    assert model.calls==0 and checkpoint(ctx,'unsafe')['cut']==1
    assert messages_to_dict(history)==original


@pytest.mark.parametrize('reject_twice',[False,True])
def test_rejected_context_recovers_once_without_replaying_tool_history(tmp_path,reject_twice):
    model=ResearchToolsModel(respond=lambda _:AIMessage('{"notes":["效率0.95，结果已保存，不重复计算。"]}'),profile={'max_input_tokens':16000})
    ctx=context(tmp_path,model);history=[HumanMessage('效率0.95')]
    for i in range(6):
        history.extend([tool('run_python',{'code':'print(6) # '+'data '*100},str(i)),ToolMessage('6',tool_call_id=str(i))])
    original=messages_to_dict(history);sent=[]
    async def handle(r):
        sent.append(r.messages)
        if len(sent)==1 or reject_twice:raise ModelContextOverflow('provider rejected')
        return AIMessage('继续原任务')
    async def run():
        return await ContextMiddleware(ctx,model,'overflow',pinned={}).awrap_model_call(Request(history),handle)
    if reject_twice:
        with pytest.raises(ModelContextOverflow):asyncio.run(run())
    else:assert asyncio.run(run()).content=='继续原任务'
    assert len(sent)==2 and model.calls==1 and len(sent[1])<len(sent[0])
    assert messages_to_dict(history)==original and not ctx.kernels._kernels


def test_huge_completed_turn_can_archive_but_pending_turn_cannot(tmp_path):
    model=ResearchToolsModel(respond=lambda _:AIMessage('{"notes":["计算已完成，结果6。"]}'),profile={'max_input_tokens':5000})
    ctx=context(tmp_path,model);history=[HumanMessage('original'),tool('run_python',{'code':'数据'*4000},'large')]
    async def handle(r):return r.messages
    with pytest.raises(ModelContextLimit):
        asyncio.run(ContextMiddleware(ctx,model,'pending',pinned={}).awrap_model_call(Request(history),handle))
    assert model.calls==0
    history.append(ToolMessage('6',tool_call_id='large'))
    sent=asyncio.run(ContextMiddleware(ctx,model,'closed',pinned={}).awrap_model_call(Request(history),handle))
    assert model.calls==1 and not any(isinstance(m,AIMessage) for m in sent)
    assert '结果6' in str(sent) and history[1].tool_calls[0]['args']['code']=='数据'*4000


def test_multiple_compactions_restore_latest_conditions_and_archive_chain(tmp_path):
    def summarize(messages):
        text=str(messages)
        assert 'private-reasoning' not in text
        return AIMessage('{"notes":["原模型日初日末相等；以用户最新效率和预报时点更正为准。"]}')
    model=ResearchToolsModel(respond=summarize,profile={'max_input_tokens':10000})
    ctx=context(tmp_path,model);history=[HumanMessage('初始效率0.95；E0=E144=6000 kWh')]
    states=[];sent=[]
    async def handle(r):sent.append(r.messages)
    async def run():
        for epoch in range(4):
            if epoch:history.append(HumanMessage(f'更正{epoch}：效率0.{95-epoch}，只允许使用0/6/12/18时已发布预报'))
            for i in range(10):
                call_id=f'{epoch}-{i}'
                ai=tool('run_python',{'code':'计算'*500},call_id)
                ai.content=[{'type':'thinking','thinking':'private-reasoning'}, {'type':'text','text':'保持能量守恒。'}]
                ai.additional_kwargs['reasoning_content']='private-reasoning'
                history.extend([ai,ToolMessage(f'本轮已计算{call_id}',tool_call_id=call_id)])
            original=messages_to_dict(history)
            await ContextMiddleware(ctx,model,'long',pinned={'epoch':epoch}).awrap_model_call(Request(history),handle)
            assert messages_to_dict(history)==original
            states.append(checkpoint(ctx,'long'))
            # A process restart with identical history must reuse the projection.
            await ContextMiddleware(ctx,model,'long',pinned={'epoch':epoch}).awrap_model_call(Request(history),handle)
            assert messages_to_dict(sent[-1])==messages_to_dict(sent[-2])
    asyncio.run(run())
    assert model.calls==4 and all(a['cut']<b['cut'] for a,b in zip(states,states[1:]))
    assert all(f'更正{i}' in str(sent[-1]) for i in range(1,4))
    assert sent[-1][-2].additional_kwargs['reasoning_content']=='private-reasoning'
    assert sent[-1][-2].content[0]['type']=='thinking'
    for previous,current in zip(states,states[1:]):
        header=json.loads(ctx.fs.read_text(current['archive_path']).splitlines()[0])
        assert header['previous_archive']==previous['archive_path']
        assert header['range'][0]==previous['cut']
        assert header['range'][1]==current['cut']
    assert not ctx.kernels._kernels


@pytest.mark.usefixtures('offline_kernel')
def test_real_client_overflow_compacts_and_resumes_without_executing_code_twice(tmp_path):
    import httpx
    from ai_system.modules.registry import invoke
    from ai_system.core_handlers.llm_providers import MeteredChatOpenAI
    from ai_system.research.runtime import budget_scope,ResearchRuntime
    from helpers.model_response import response
    requests=[];main=0
    def wire(request):
        nonlocal main
        payload=json.loads(request.content);requests.append(payload)
        if 'JSON Schema' in str(payload['messages'][-1]['content']):
            result=response();result['choices'][0]['message']['content']='{"notes":["已执行计数并保存结果，不重复运行。"]}'
            return httpx.Response(200,json=result)
        main+=1
        if main==1:
            return httpx.Response(400,json={'error':{'code':'context_length_exceeded','message':'too many tokens'}})
        return httpx.Response(200,json=response())
    llm=MeteredChatOpenAI(model='offline',api_key='offline',base_url='https://offline.invalid/v1',max_tokens=1800,
        profile={'max_input_tokens':16000},http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(wire)))
    ctx=context(tmp_path,llm)
    code="from pathlib import Path\np=Path('outputs/count.txt')\np.parent.mkdir(exist_ok=True)\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')\nprint(p.read_text())"
    async def run():
        result=await invoke(ctx,'research.run_python',code=code,title='只运行一次')
        history=[HumanMessage('计算一次，保留结果'),tool('research_run_python',{'code':code},'once'),
                 ToolMessage(json.dumps(result,ensure_ascii=False),tool_call_id='once')]
        original=messages_to_dict(history)
        async def handle(r):return await llm.ainvoke(r.messages)
        with budget_scope(tmp_path,ctx.research.research_id):
            reply=await ContextMiddleware(ctx,llm,'wire',pinned={}).awrap_model_call(Request(history),handle)
        assert reply.content=='done' and messages_to_dict(history)==original
        assert len(ctx.fs._manifest['runs'])==1
        assert ctx.fs.read_text('outputs/count.txt')=='1'
    asyncio.run(run())
    assert main==2 and len(requests)==3
    recorded=ResearchRuntime(tmp_path,ctx.research.research_id).requests()
    assert [r['state'] for r in recorded]==['failed','completed','completed']
    assert recorded[0]['diagnostic']['error_kind']=='context_limit'


@pytest.mark.parametrize('status,code,expected',[(400,'invalid_parameter','service_error'),
    (401,'context_length_exceeded','authentication'),(429,'rate_limit_exceeded','rate_limit'),
    (400,'context_length_exceeded','context_limit'),(413,'context_window_exceeded','context_limit')])
def test_only_explicit_rejected_context_is_classified_for_compaction(status,code,expected):
    from types import SimpleNamespace
    from ai_system.core_handlers.model_runtime import failure
    assert failure(SimpleNamespace(status_code=status,body={'error':{'code':code}}))[0]==expected


def test_long_archived_argument_is_retrievable_with_valid_paginated_file_calls(tmp_path):
    from ai_system.modules.registry import invoke
    model=ResearchToolsModel(respond=lambda _:AIMessage('invalid'),profile={'max_input_tokens':8000})
    ctx=context(tmp_path,model);code='计算'*18000
    history=[HumanMessage('original'),tool('run_python',{'code':code},'large'),ToolMessage('6',tool_call_id='large')]
    async def handle(r):return r.messages
    async def run():
        await ContextMiddleware(ctx,model,'pages',pinned={}).awrap_model_call(Request(history),handle)
        saved=checkpoint(ctx,'pages')
        source=next(r for r in saved['fallback'] if r.get('tool_calls'))['source']
        assert source['limit']<=24000 and source['end']-source['start']>24000
        chunks=[];offset=source['start']
        while offset<source['end']:
            result=await invoke(ctx,'research.read_artifact',path=source['path'],start=offset,
                                limit=min(source['limit'],source['end']-offset))
            chunks.append(result['excerpt']);offset+=len(result['excerpt'])
        assert json.loads(''.join(chunks))['tool_calls'][0]['args']['code']==code
    asyncio.run(run())
    assert model.calls==1


def test_parallel_pending_tools_and_required_reasoning_remain_atomic(tmp_path):
    model=ResearchToolsModel(respond=lambda _:AIMessage('{"notes":["仅使用已发布预报。"]}'),profile={'max_input_tokens':10000})
    ctx=context(tmp_path,model);history=[HumanMessage('不使用未来实际数据')]
    for i in range(10):
        history.extend([tool('run_python',{'code':'计算'*500},str(i)),ToolMessage('done',tool_call_id=str(i))])
    parallel=AIMessage('',tool_calls=[{'name':'read_file','args':{'path':f'{n}.txt'},'id':n} for n in ['a','b']],
                       additional_kwargs={'reasoning_content':'required-private-reasoning'})
    history.extend([parallel,ToolMessage('first reply',tool_call_id='a')])
    original=messages_to_dict(history)
    async def handle(r):return r.messages
    sent=asyncio.run(ContextMiddleware(ctx,model,'parallel',pinned={}).awrap_model_call(Request(history),handle))
    assert messages_to_dict(sent[-2:])==messages_to_dict(history[-2:])
    assert messages_to_dict(history)==original
    assert checkpoint(ctx,'parallel')['cut']<=len(history)-2


def test_partial_generation_context_error_is_not_replayed_as_a_rejected_request(tmp_path):
    model=ResearchToolsModel(respond=lambda _:pytest.fail('No compaction for partial generation'),profile={'max_input_tokens':16000})
    ctx=context(tmp_path,model);calls=[]
    async def handle(r):calls.append(r);raise ModelContextLimit('generation already started')
    with pytest.raises(ModelContextLimit):
        asyncio.run(ContextMiddleware(ctx,model,'partial',pinned={}).awrap_model_call(Request([HumanMessage('work')]),handle))
    assert len(calls)==1 and model.calls==0
