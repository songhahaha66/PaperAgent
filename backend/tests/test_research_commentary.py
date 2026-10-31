"""Public progress stays ordered with real tools and never republishes reasoning."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from langchain_core.messages import AIMessage
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.graph.commentary import ProgressMiddleware, public_progress
from helpers.conversation import writer, call


def test_model_progress_commits_before_tool_and_survives_refresh(tmp_path):
    manager, root, reply, updates = writer(tmp_path)
    class Events:
        async def emit(self, kind, content): reply.block({'type':kind,'content':content})
    middleware = ProgressMiddleware(Events(), scope='experiment:E1')
    response = SimpleNamespace(result=[AIMessage(id='msg-1', content='<progress>I will inspect the data.</progress>',
        tool_calls=[{'name':'read_file','args':{},'id':'tool-1','type':'tool_call'}])])
    async def handler(_): return response
    async def scenario():
        assert await middleware.awrap_model_call(None, handler) is response
        # The model wrapper completes before the agent dispatches its tool.
        assert manager.get_messages('work')[0]['segments'][0]['text'] == 'I will inspect the data.'
        reply.execution(root,'r','activity',call())
        await middleware.awrap_model_call(None, handler)
        response.result = [AIMessage(id='msg-2',content='<progress>The file contains 20 rows.</progress>')]
        await middleware.awrap_model_call(None,handler)
        reply.execution(root,'r','activity',call(status='completed',finished_at=2))
        reply.finish('completed')
    asyncio.run(scenario())
    stored = manager.get_messages('work')[0]
    assert [s['type'] for s in stored['segments']] == ['text','execution','text']
    assert stored['segments'][0]['phase'] == 'commentary'
    assert stored['segments'][0]['scope'] == 'experiment:E1'
    assert stored['segments'][1]['execution']['status'] == 'completed'
    assert len(stored['segments']) == 3
    assert all('<progress>' not in str(u) for u in updates)


def test_plain_public_messages_are_durable_ordered_and_do_not_change_model_history(tmp_path):
    manager, root, reply, updates = writer(tmp_path)
    class Events:
        async def emit(self, kind, content): reply.block({'type':kind,'content':content})
    middleware = ProgressMiddleware(Events(), scope='research')
    first = AIMessage(id='plain-1', content='先检查附件，再计算。',
        additional_kwargs={'reasoning_content':'private reasoning'},
        tool_calls=[{'name':'read_file','args':{'path':'input.csv'},'id':'read','type':'tool_call'}])
    second = AIMessage(id='plain-2', content='发现约束方向错误，正在修正。' + '这是公开说明。' * 150)
    original = first.model_dump()
    response = SimpleNamespace(result=[first])
    async def handler(_): return response
    async def scenario():
        await middleware.awrap_model_call(None, handler)
        reply.execution(root,'r','activity',call('read'))
        reply.execution(root,'r','activity',call('read',status='completed',finished_at=2))
        response.result = [second]
        await middleware.awrap_model_call(None, handler)
        await middleware.awrap_model_call(None, handler)
        reply.execution(root,'r','activity',call('retry'))
        reply.finish('paused')
    asyncio.run(scenario())
    saved = manager.get_messages('work')[0]
    assert [s['type'] for s in saved['segments']] == ['text','execution','text','execution']
    assert saved['segments'][0]['text'] == first.content
    assert saved['segments'][2]['text'] == second.content
    assert 'private reasoning' not in json.dumps(saved, ensure_ascii=False)
    assert first.model_dump() == original
    assert any(segment.get('text') == first.content for update in updates for segment in update['segments'])


def test_public_text_blocks_exclude_reasoning_and_accept_legacy_progress():
    assert public_progress([
        {'type':'reasoning','text':'private block'},
        {'type':'text','text':'<think>private tagged</think>开始处理。'},
        {'type':'text','text':'<progress>正在计算。</progress>'},
    ]) == ['开始处理。\n正在计算。']
    assert public_progress('公开说明。<analysis>unfinished private text') == ['公开说明。']
    assert public_progress('<reasoning>private <progress>do not publish</progress></reasoning>') == []


def test_final_answer_replaces_matching_commentary_in_live_updates_and_history(tmp_path):
    manager, root, reply, updates = writer(tmp_path)
    reply.commentary({'id': 'a' * 64, 'text': '这是当前结果的解释。', 'scope': 'research'})
    commentary_id = updates[-1]['segments'][0]['id']
    reply.block({'type': 'main_agent_complete', 'content': '这是当前结果的解释。'})
    reply.finish('completed')
    saved = manager.get_messages('work')[0]
    assert saved['content'] == '这是当前结果的解释。'
    assert len(saved['segments']) == 1
    assert saved['segments'][0]['id'] == commentary_id
    assert saved['segments'][0]['block']['type'] == 'main_agent_complete'
    assert any(row['id'] == commentary_id and row['type'] == 'event'
               for update in updates for row in update['segments'])


def test_real_agent_publishes_plain_text_around_its_tool_call(tmp_path):
    from langchain_core.messages import ToolMessage
    from ai_system.research.loop import prepare_action
    from helpers.context import session_context
    from research_tools_helpers import ResearchToolsModel, tool
    from services.chat_services.conversation_timeline import TIMELINE

    manager, root, reply, updates = writer(tmp_path)
    def respond(messages):
        after_tool = any(isinstance(message, ToolMessage) for message in messages)
        message = (tool('research_pause', {'message':'检查完成，等待下一步。'}, 'pause')
                   if after_tool else tool('research_list_files', {}, 'list'))
        message.content = '文件已检查，暂时停止。' if after_tool else '先检查已有文件。'
        message.additional_kwargs['reasoning_content'] = 'private reasoning'
        return message

    ctx = session_context(root, ResearchToolsModel(respond=respond))
    class Stream:
        async def send_json_block(self, kind, content):
            if kind == 'research_commentary':
                reply.block({'type':kind, 'content':content})
    ctx.events.stream_manager = Stream()

    async def scenario():
        token = TIMELINE.set(reply)
        try:
            assert await prepare_action(ctx)
            reply.finish('paused')
        finally:
            TIMELINE.reset(token)
            await ctx.kernels.close()
    asyncio.run(scenario())
    stored = manager.get_messages('work')[0]
    segments = stored['segments']
    before = next(i for i, row in enumerate(segments) if row.get('text') == '先检查已有文件。')
    after = next(i for i, row in enumerate(segments) if row.get('text') == '文件已检查，暂时停止。')
    assert any(row['type'] == 'execution' for row in segments[before + 1:after])
    assert 'private reasoning' not in json.dumps(stored, ensure_ascii=False)
    assert any(row.get('text') == '先检查已有文件。' for update in updates for row in update['segments'])


def test_failed_direct_execution_is_failed_activity_even_when_call_returns(tmp_path):
    from ai_system.graph.context import GraphContext
    from ai_system.research.store import ResearchStore
    from ai_system.modules.registry import invoke
    from services.chat_services.conversation_timeline import TIMELINE
    manager,root,reply,_=writer(tmp_path)
    ctx=GraphContext.create(str(root),planner_llm=None)
    ctx.research=ResearchStore(root,'failed-direct')
    ctx.research.initialize('Compute a small sample mean.')
    async def scenario():
        token=TIMELINE.set(reply)
        try:
            result=await invoke(ctx,'research.run_python',code='pass',title='计算样本均值')
            assert '占位' in result['observation']
        finally:
            TIMELINE.reset(token)
            await ctx.kernels.close()
    asyncio.run(scenario())
    assert not ctx.research.snapshot()['experiments']
    row=next(s['execution'] for s in manager.get_messages('work')[0]['segments']
             if s.get('execution',{}).get('capability')=='research.run_python')
    assert row['status']=='failed' and row['finished_at']
    assert '占位' in row['message']
