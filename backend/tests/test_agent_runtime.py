"""Protocol invariants at real dispatch boundaries, without models or network."""
import asyncio
import json
from contextlib import contextmanager
from copy import deepcopy

import pytest
from ai_system.agent.context import CURRENT_TURN, new_turn, session_turn
from ai_system.agent.runtime import ToolInvocation, ToolResult, dispatch, update_tool
from ai_system.agent.protocol import TOOL_TYPES
from ai_system.agent.store import AgentStore
from helpers.context import session_context


@contextmanager
def running(ctx, publish=None):
    turn = new_turn(ctx.workspace,'lead',research_id=ctx.research.research_id,publish=publish)
    token = CURRENT_TURN.set(turn)
    try:yield turn
    finally:CURRENT_TURN.reset(token)


def test_tool_result_is_separate_and_events_are_committed_before_publish(tmp_path):
    ctx=session_context(tmp_path);events=[]
    def publish(event):
        if event.get('item'):
            stored=AgentStore(tmp_path,read_only=True).get_item(event['item']['id'])
            assert stored==event['item']
        events.append(deepcopy(event))
    async def run():
        with running(ctx,publish) as turn:
            async def handler():
                update_tool(ctx,output={'output_tail':'bounded progress'},phase='compute')
                return ToolResult({'private_report':'MODEL_ONLY','value':42})
            result=await dispatch(ctx,ToolInvocation('run_python','one',{'code':'PRIVATE_CODE'}),handler)
            assert result.value['value']==42
            turn.finish('completed')
    asyncio.run(run())
    items=AgentStore(tmp_path).items()
    assert len(items)==1 and items[0]['type']=='commandExecution'
    assert items[0]['output_tail']=='bounded progress'
    assert 'MODEL_ONLY' not in json.dumps(items) and 'PRIVATE_CODE' not in json.dumps(events)
    lifecycle=[e['method'] for e in events if e.get('item')]
    assert lifecycle==['item/started','item/updated','item/completed']
    assert len({e['item']['id'] for e in events if e.get('item')})==1


def test_parallel_reads_exclude_writes_and_cancelled_waiter_finishes(tmp_path):
    ctx=session_context(tmp_path)
    async def run():
        with running(ctx) as turn:
            entered=asyncio.Event();release=asyncio.Event();observed=[];readers=0
            async def read():
                nonlocal readers
                readers+=1
                if readers==2:entered.set()
                await release.wait()
                readers-=1
                return 'read'
            first=asyncio.create_task(dispatch(ctx,ToolInvocation('read','r1',{},'read'),read))
            second=asyncio.create_task(dispatch(ctx,ToolInvocation('read','r2',{},'read'),read))
            await asyncio.wait_for(entered.wait(),2)
            async def write():
                assert readers==0
                observed.append('write')
                return 'written'
            cancelled=asyncio.create_task(dispatch(ctx,ToolInvocation('write','cancel',{}),write))
            writer=asyncio.create_task(dispatch(ctx,ToolInvocation('write','w',{}),write))
            await asyncio.sleep(0)
            assert not observed
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):await cancelled
            release.set()
            await asyncio.gather(first,second,writer)
            assert observed==['write']
            items=turn.store.items(turn_id=turn.turn_id)
            assert next(i for i in items if i['call_id']=='cancel')['status']=='interrupted'
            assert all(i['status']!='running' for i in items)
            turn.finish('completed')
    asyncio.run(run())


def test_duplicate_call_executes_once_and_binds_arguments_before_receipt(tmp_path):
    ctx=session_context(tmp_path);count=0
    async def run():
        nonlocal count
        with running(ctx) as turn:
            async def handler():
                nonlocal count
                count+=1
                await asyncio.sleep(.01)
                return {'n':count}
            invocation=ToolInvocation('read','same',{'path':'one'},'read')
            results=await asyncio.gather(*(dispatch(ctx,invocation,handler) for _ in range(2)))
            assert [r.value for r in results]==[{'n':1},{'n':1}] and count==1
            with pytest.raises(ValueError,match='不能更改参数'):
                await dispatch(ctx,ToolInvocation('read','same',{'path':'two'},'read'),handler)
            assert len(turn.store.items(turn_id=turn.turn_id))==1
            # Even an interrupted invocation has a durable argument binding.
            async def stop():raise asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError):
                await dispatch(ctx,ToolInvocation('read','interrupted',{'path':'one'}),stop)
            with pytest.raises(ValueError,match='不能更改参数'):
                await dispatch(ctx,ToolInvocation('read','interrupted',{'path':'two'}),handler)
    asyncio.run(run())


def test_child_thread_is_explicit_and_helpers_do_not_create_items(tmp_path):
    ctx=session_context(tmp_path)
    child=session_context(tmp_path/'child');child.research=ctx.research
    async def run():
        with running(ctx) as turn:
            async def parent():
                update_tool(ctx,phase='preparing')
                with session_turn(child,'child-session'):
                    await dispatch(child,ToolInvocation('read_file','child-call',{},'read'),lambda:'child result')
                update_tool(ctx,phase='delivery')
                return 'parent result'
            await dispatch(ctx,ToolInvocation('research.delegate','parent',{},'delegate'),parent)
            rows=turn.store.items(root_turn_id=turn.turn_id)
            assert len(rows)==2
            parent_item=next(i for i in rows if i['type']=='collabToolCall')
            child_item=next(i for i in rows if i['type']=='toolCall')
            assert parent_item['child_thread_id']==child_item['thread_id']
            assert parent_item['child_turn_id']==child_item['turn_id']
            assert parent_item['turn_id']!=child_item['turn_id']
            assert not any('parent_call_id' in i for i in rows)
    asyncio.run(run())


def test_plan_is_a_typed_item_and_invalid_tool_call_is_visible(tmp_path):
    from ai_system.modules.registry import capabilities
    from ai_system.agent.runtime import invoke
    ctx=session_context(tmp_path)
    async def run():
        with running(ctx) as turn:
            result=await invoke(ctx,capabilities(),'research.update_plan',{'steps':[{'title':'核对数据','status':'in_progress'}]},call_id='plan')
            assert result.value['progress_plan']['steps'][0]['title']=='核对数据'
            with pytest.raises(ValueError):await invoke(ctx,capabilities(),'unknown',{},call_id='unknown')
            plan,failed=turn.store.items(turn_id=turn.turn_id)
            assert plan['type']=='plan' and plan['steps'][0]['step']=='核对数据'
            assert failed['type']=='toolCall' and failed['status']=='failed' and failed['message']
    asyncio.run(run())


def test_terminal_item_cannot_regress_and_stale_cache_rebuilds_from_store(tmp_path):
    from helpers.conversation import writer,record,call
    from services.chat_services.conversation_timeline import public_reply
    history,root,reply,_=writer(tmp_path)
    record(reply,call())
    record(reply,call(status='completed',finished_at=3))
    record(reply,call())
    reply.finish('completed')
    cached=deepcopy(history.get_messages('work')[0])
    cached['items'][0]['status']='running'
    assert public_reply(cached,root=root)['items'][0]['status']=='completed'
    assert len(public_reply(cached,root=root)['items'])==1


def test_item_cannot_claim_another_thread_or_publish_after_turn_ends(tmp_path):
    from ai_system.agent.protocol import MessageItem
    turn=new_turn(tmp_path,'owner')
    item=MessageItem(id='message',thread_id='other',turn_id=turn.turn_id,
                     type='agentMessage',text='hello',started_at=1).model_dump()
    with pytest.raises(ValueError,match='thread must match'):
        turn.put(item,'item/started')
    assert turn.store.items()==[]
    turn.finish('completed')
    item['thread_id']='owner'
    with pytest.raises(ValueError,match='outside a running Turn'):
        turn.put(item,'item/started')
