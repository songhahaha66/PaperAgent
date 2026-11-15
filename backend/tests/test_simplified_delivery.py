from ai_system.agent.store import AgentStore
"""Real state/Agent/export paths with offline models and compiler fixtures."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from ai_system.agent.agent import PaperAgent
from ai_system.graph.context import GraphContext
from ai_system.modules.registry import invoke, capabilities
from ai_system.research import operations
from ai_system.research.store import ResearchStore
from ai_system.research.views import questions
from services.file_services.workspace_layout import workspace_path
from research_tools_helpers import ResearchToolsModel
from research_tools_helpers import tool


def context(root, mode='markdown'):
    ctx=GraphContext.create(str(root),planner_llm=None,output_mode=mode)
    ctx.research=ResearchStore(root,'simplified')
    ctx.research.initialize('说明当前已有结果和未完成部分',output_mode=mode)
    return ctx


@pytest.mark.parametrize('legacy_stage',[None,'validate','render'])
@pytest.mark.parametrize('mode',['markdown','word'])
@pytest.mark.usefixtures('offline_compiler')
def test_one_export_delivers_existing_file_without_answer_registration(tmp_path,legacy_stage,mode):
    ctx=context(tmp_path,mode)
    operations.update_question(ctx,{'question':'还未记录答案的问题','title':'问题一'})
    (tmp_path/'paper.md').write_text('# 当前结果\n已得到部分结果，其他部分尚未完成。')
    result=asyncio.run(invoke(ctx,'research.deliver',title='部分结果',reason='交付已有稿件'))
    state=ctx.research.snapshot()
    assert not result.get('issues') and state['publication'] is None
    assert state['outcome']!='completed', 'the shared loop owns request completion'
    assert state['exports']==['paper.md' if mode=='markdown' else 'paper.docx']
    assert set(state['delivery_stages'])=={'export'}
    assert not {'judgment','coverage','answer_gaps'} & questions(state)[0].keys()
    assert not workspace_path(tmp_path,'.system/validation.json').exists()
    assert not state['executions'] and not ctx.kernels._kernels


def test_missing_image_keeps_export_blocked_without_a_review_model(tmp_path):
    ctx=context(tmp_path)
    (tmp_path/'paper.md').write_text('# 当前结果\n![missing](outputs/missing.png)')
    result=asyncio.run(invoke(ctx,'writing.export'))
    assert '图片文件不可用' in str(result['issues'])
    assert ctx.research.snapshot()['delivery_stages']['export']['status']=='blocked'
    names={c['name'] for c in capabilities().describe()}
    assert not {'writing.validate','writing.render'} & names


def test_followups_share_agent_history_and_do_not_force_export_or_restart(tmp_path):
    ctx=context(tmp_path);store=ctx.research;store.set_outcome('completed')
    thread=workspace_path(tmp_path,'.system/graph_thread.json')
    thread.write_text(json.dumps({'thread_id':store.research_id,'engine_version':'research-v3','status':'done'}))
    AgentStore(tmp_path)
    seen=[]
    def respond(messages):
        seen.append([m.content for m in messages if isinstance(m,HumanMessage)])
        return AIMessage(content='这是当前结果的解释，已有计算未重做。')
    agent=PaperAgent(ResearchToolsModel(respond=respond),workspace_dir=str(tmp_path))
    async def run():
        await agent.send_message('请解释当前结果')
        await agent.send_message('再说明适用范围')
    asyncio.run(run())
    state=store.snapshot()
    assert state['research_id']==store.research_id and state['outcome']=='completed'
    assert state.get('last_reply') and not state.get('conversation_input')
    assert state['publication'] is None and not state['executions']
    assert sum(s=='请解释当前结果' for s in seen[-1])==1
    assert sum(s=='再说明适用范围' for s in seen[-1])==1
    assert not (tmp_path/'paper.md').exists()


def test_waiting_user_answer_reaches_the_same_agent_once(tmp_path):
    seen=[]
    def respond(messages):
        seen.append(messages)
        if any(isinstance(m,HumanMessage) and m.content=='使用2025年的数据' for m in messages):
            return AIMessage(content='收到，当前范围是2025年。')
        return tool('research_ask_user',{'message':'使用哪一年的数据？'},'ask-year')
    agent=PaperAgent(ResearchToolsModel(respond=respond),workspace_dir=str(tmp_path))
    async def run():
        assert '哪一年' in await agent.run('先确认数据范围')
        original=agent._load_thread()
        assert '2025年' in await agent.run('使用2025年的数据',action='answer')
        assert agent._load_thread()==original
    asyncio.run(run())
    assert sum(isinstance(m,HumanMessage) and m.content=='使用2025年的数据' for m in seen[-1])==1


def test_followup_final_reply_never_exposes_tagged_reasoning(tmp_path):
    ctx=context(tmp_path);store=ctx.research;store.set_outcome('completed')
    workspace_path(tmp_path,'.system/graph_thread.json').write_text(
        json.dumps({'thread_id':store.research_id,'engine_version':'research-v3','status':'done'}))
    AgentStore(tmp_path)
    model=ResearchToolsModel(respond=lambda _: AIMessage(content='<think>private analysis</think>结果只适用于当前样本。'))
    agent=PaperAgent(model,workspace_dir=str(tmp_path))
    reply=asyncio.run(agent.send_message('结果适用范围是什么？'))
    assert reply=='结果只适用于当前样本。'
    assert store.snapshot()['last_reply']==reply


def test_failed_conversation_turn_survives_recovery_without_duplicate_input(tmp_path):
    ctx=context(tmp_path);store=ctx.research;store.set_outcome('completed')
    workspace_path(tmp_path,'.system/graph_thread.json').write_text(json.dumps(
        {'thread_id':store.research_id,'engine_version':'research-v3','status':'done'}))
    AgentStore(tmp_path)
    seen=[]
    def respond(messages):
        seen.append(messages)
        if len(seen)==1:raise ConnectionError('controlled interruption')
        return AIMessage(content='恢复后继续解释已有结果。')
    agent=PaperAgent(ResearchToolsModel(respond=respond),workspace_dir=str(tmp_path))
    async def run():
        with pytest.raises(ConnectionError,match='controlled interruption'):
            await agent.send_message('说明当前结果的限制')
        assert store.snapshot()['conversation_input']['message']=='说明当前结果的限制'
        await agent.resume_failed()
    asyncio.run(run())
    assert not store.snapshot().get('conversation_input')
    assert sum(isinstance(m,HumanMessage) and m.content=='说明当前结果的限制' for m in seen[-1])==1
    assert not store.snapshot()['executions']


def test_completed_message_id_is_not_executed_twice(tmp_path):
    ctx=context(tmp_path);store=ctx.research;store.set_outcome('completed')
    workspace_path(tmp_path,'.system/graph_thread.json').write_text(json.dumps(
        {'thread_id':store.research_id,'engine_version':'research-v3','status':'done'}))
    AgentStore(tmp_path)
    model=ResearchToolsModel(respond=lambda _: AIMessage(content='已处理本轮要求。'))
    agent=PaperAgent(model,workspace_dir=str(tmp_path))
    async def run():
        first=await agent.send_message('处理新要求',message_id='user:stable')
        assert await agent.send_message('处理新要求',message_id='user:stable') == first
    asyncio.run(run())
    assert model.calls==1


def test_consumed_turn_retry_resumes_delivery_without_reinjecting_request(tmp_path):
    from ai_system.research.store import digest
    ctx=context(tmp_path);store=ctx.research;store.set_outcome('completed')
    workspace_path(tmp_path,'.system/graph_thread.json').write_text(json.dumps(
        {'thread_id':store.research_id,'engine_version':'research-v3','status':'done'}))
    AgentStore(tmp_path)
    with store._transaction() as db:
        db.execute('INSERT INTO commands VALUES (?,?,?,?)',(store.research_id,'conversation:user:consumed',
            digest('修改并导出'),json.dumps({'message':'修改并导出','reply':None})))
    def forbidden(_):
        pytest.fail('an already consumed turn must not ask the model to modify the paper again')
    model=ResearchToolsModel(respond=forbidden)
    agent=PaperAgent(model,workspace_dir=str(tmp_path))
    async def run():
        first=await agent.send_message('修改并导出',message_id='user:consumed')
        assert await agent.send_message('修改并导出',message_id='user:consumed')==first
    asyncio.run(run())
    assert model.calls==0 and not store.snapshot().get('conversation_input')


def test_amendment_supersedes_pending_question_and_keeps_one_revision(tmp_path):
    ctx=context(tmp_path);store=ctx.research
    workspace_path(tmp_path,'.system/graph_thread.json').write_text(json.dumps(
        {'thread_id':store.research_id,'engine_version':'research-v3','status':'interrupted'}))
    AgentStore(tmp_path)
    with store._transaction() as db:
        state=store._read(db)
        state['pending_input']={'id':'ask-year','message':'使用哪一年？'}
        store._save(db,state,'test_waiting',{})
    original=store.snapshot()
    model=ResearchToolsModel(respond=lambda _: AIMessage(content='已记录新范围，原问题无需再回答。'))
    agent=PaperAgent(model,workspace_dir=str(tmp_path))
    async def run():
        options=dict(command_id='amend-one',research_id=store.research_id,base_revision=original['revision'],
                     change='不按年份筛选，使用所有已有数据',replace_pending=True)
        await agent.revise_research(**options)
        await agent.revise_research(**options)
    asyncio.run(run())
    state=store.snapshot()
    assert state['spec']['amendments']==['不按年份筛选，使用所有已有数据']
    assert state.get('clarifications',[])==[], 'a changed requirement is not an answer to the old question'
    assert state.get('last_reply') and not state.get('conversation_input')
    assert model.calls==1


def test_new_user_turn_preempts_tools_scheduled_by_old_turn(tmp_path):
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import StructuredTool
    from ai_system.agent.loop import AgentLoop,run_session
    from ai_system.agent.store import AgentStore
    calls=[]
    async def write_marker(value: str):
        calls.append(value);return value
    def respond(messages):
        assert any(m.id=='new-turn' for m in messages)
        assert any(isinstance(m,ToolMessage) and m.tool_call_id=='old-write' for m in messages)
        return AIMessage(content='已按新要求停止旧操作。')
    model=ResearchToolsModel(respond=respond);ctx=context(tmp_path)
    store=AgentStore(tmp_path)
    store.append_message('lead',HumanMessage(content='旧要求',id='old-turn'))
    store.append_message('lead',tool('write_marker',{'value':'must-not-execute'},'old-write'))
    agent=AgentLoop(model,[StructuredTool.from_function(coroutine=write_marker,description='Write a marker')],
                    'Follow the latest user request.',ctx=ctx)
    result=asyncio.run(run_session(agent,'lead',[HumanMessage(content='先别执行旧操作',id='new-turn')],ctx=ctx,new_user_turn_id='new-turn'))
    assert result['messages'][-1].content=='已按新要求停止旧操作。'
    assert calls==[] and model.calls==1


@pytest.mark.usefixtures('offline_compiler')
def test_word_export_preserves_old_output_if_source_changes_during_conversion(tmp_path,monkeypatch):
    from ai_system.export import docx
    (tmp_path/'paper.md').write_text('# Original\nOriginal text')
    (tmp_path/'paper.docx').write_bytes(b'previous delivered file')
    original=docx.pandoc
    async def change_source(*args,**kwargs):
        result=await original(*args,**kwargs)
        (tmp_path/'paper.md').write_text('# User edit\nNew text')
        return result
    monkeypatch.setattr(docx,'pandoc',change_source)
    import hashlib
    text=(tmp_path/'paper.md').read_text()
    with pytest.raises(RuntimeError,match='正文在导出期间改变'):
        asyncio.run(docx.export_docx(tmp_path,text,source_version=hashlib.sha256(text.encode()).hexdigest()))
    assert (tmp_path/'paper.docx').read_bytes()==b'previous delivered file'
