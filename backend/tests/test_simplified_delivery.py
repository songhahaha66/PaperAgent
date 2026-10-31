"""Real state/Agent/export paths with offline models and compiler fixtures."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from ai_system.graph.agent import PaperGraphAgent
from ai_system.graph.context import GraphContext
from ai_system.modules.registry import invoke, capabilities
from ai_system.research import operations, loop
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
    if legacy_stage:
        with ctx.research._transaction() as db:
            state=ctx.research._read(db);state['publication']={'stage':legacy_stage}
            action=loop._set_action(state,'writing.'+legacy_stage,{},'恢复旧交付')
            ctx.research._save(db,state,'fixture_old_delivery',{})
    else:
        operations.deliver(ctx,{'title':'部分结果','reason':'交付已有稿件'})
        action=ctx.research.snapshot()['action']
        assert action['capability']=='writing.export'
    asyncio.run(loop._act({'action_id':action['id']},SimpleNamespace(context=ctx)))
    state=ctx.research.snapshot()
    assert state['outcome']=='completed' and state['publication']['stage']=='completed'
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
    workspace_path(tmp_path,'.system/graph.sqlite').touch()
    seen=[]
    def respond(messages):
        seen.append([m.content for m in messages if isinstance(m,HumanMessage)])
        return AIMessage(content='这是当前结果的解释，已有计算未重做。')
    agent=PaperGraphAgent(ResearchToolsModel(respond=respond),workspace_dir=str(tmp_path))
    async def run():
        await agent.send_message('请解释当前结果')
        await agent.send_message('再说明适用范围')
    asyncio.run(run())
    state=store.snapshot()
    assert state['research_id']==store.research_id and state['outcome']=='completed'
    assert state['action']['capability']=='reply' and not state.get('conversation_input')
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
    agent=PaperGraphAgent(ResearchToolsModel(respond=respond),workspace_dir=str(tmp_path))
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
    workspace_path(tmp_path,'.system/graph.sqlite').touch()
    model=ResearchToolsModel(respond=lambda _: AIMessage(content='<think>private analysis</think>结果只适用于当前样本。'))
    agent=PaperGraphAgent(model,workspace_dir=str(tmp_path))
    reply=asyncio.run(agent.send_message('结果适用范围是什么？'))
    assert reply=='结果只适用于当前样本。'
    assert store.snapshot()['action']['result']['reply']==reply


def test_failed_conversation_turn_survives_recovery_without_duplicate_input(tmp_path):
    ctx=context(tmp_path);store=ctx.research;store.set_outcome('completed')
    workspace_path(tmp_path,'.system/graph_thread.json').write_text(json.dumps(
        {'thread_id':store.research_id,'engine_version':'research-v3','status':'done'}))
    workspace_path(tmp_path,'.system/graph.sqlite').touch()
    seen=[]
    def respond(messages):
        seen.append(messages)
        if len(seen)==1:raise ConnectionError('controlled interruption')
        return AIMessage(content='恢复后继续解释已有结果。')
    agent=PaperGraphAgent(ResearchToolsModel(respond=respond),workspace_dir=str(tmp_path))
    async def run():
        await agent.send_message('说明当前结果的限制')
        assert store.snapshot()['conversation_input']['message']=='说明当前结果的限制'
        await agent.resume_failed()
    asyncio.run(run())
    assert not store.snapshot().get('conversation_input')
    assert sum(isinstance(m,HumanMessage) and m.content=='说明当前结果的限制' for m in seen[-1])==1
    assert not store.snapshot()['executions']


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
