"""Context pressure, logical files and natural final replies across real boundaries."""
import asyncio
from types import SimpleNamespace
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from ai_system.graph.compaction import ContextMiddleware, WorkNotes, summary_model
from ai_system.modules.registry import invoke
from ai_system.research.store import ResearchStore
from ai_system.research.runtime import execution_links
from helpers.context import delivery_context as context, ContextRequest as Request
from research_tools_helpers import ResearchToolsModel, tool
from services.file_services.workspace_fs import WorkspaceFS


def test_full_context_budget_includes_system_facts_and_summary_once(tmp_path):
    model=ResearchToolsModel(respond=lambda _:AIMessage(content='{"notes":["用原始数据修正过单位换算。"]}'),profile={'max_input_tokens':10000})
    ctx=context(tmp_path,model)
    history=[HumanMessage('效率0.95')]
    for i in range(12):
        history += [tool('run_python',{'code':'计算'*500},str(i)),ToolMessage(str(i),tool_call_id=str(i))]
    system=SystemMessage('固定约束'*150);sent=[];usage=[]
    middleware=ContextMiddleware(ctx,model,'whole',pinned={'current':'当前事实'*80})
    async def handle(r):sent.append(r.messages);usage.append(execution_links()['context_usage'])
    async def run():
        await middleware.awrap_model_call(Request(history,system),handle)
        await middleware.awrap_model_call(Request(history,system),handle)
        # Retrieve a detail removed from the active context through the same file API.
        pointer=next(m.content for m in sent[0] if '旧轮次原文' in str(m.content)).split('旧轮次原文：')[1]
        restored=await invoke(ctx,'research.read_artifact',path=pointer)
        assert '计算' in restored['excerpt']
    asyncio.run(run())
    assert model.calls==1
    assert usage[0]['total_estimated_tokens']<=usage[0]['low_watermark']
    assert usage[0]['total_estimated_tokens']>usage[0]['messages_estimated_tokens']
    assert 'pending' not in WorkNotes.model_fields and 'files' not in WorkNotes.model_fields
    assert history[1].tool_calls[0]['args']['code']=='计算'*500


def test_unshrinkable_latest_call_is_not_rewritten_or_sent_over_budget(tmp_path):
    from ai_system.config.generation import ModelContextLimit
    model=ResearchToolsModel(respond=lambda _:pytest.fail('Do not summarize in a loop'),profile={'max_input_tokens':4000})
    ctx=context(tmp_path,model);history=[HumanMessage('original'),tool('run_python',{'code':'数据'*4000},'open')]
    async def handle(_):pytest.fail('Do not send over budget')
    with pytest.raises(ModelContextLimit,match='完整工具轮次'):
        asyncio.run(ContextMiddleware(ctx,model,'oversize').awrap_model_call(Request(history),handle))
    assert history[1].tool_calls[0]['args']['code']=='数据'*4000


def test_summary_has_a_separate_nonthinking_budget_without_changing_research():
    from ai_system.core_handlers.llm_providers import create_llm_from_model_config
    model=create_llm_from_model_config(SimpleNamespace(model_id='deepseek-flash',base_url='https://api.deepseek.com',
                                                       api_key='offline',thinking_enabled=True))
    note=summary_model(model)
    assert note.max_tokens==1800 and note.extra_body['thinking']['type']=='disabled'
    assert model.max_tokens==16000 and model.extra_body['thinking']['type']=='enabled'


def test_backend_manuscript_and_inputs_share_paginated_versioned_file_tools(tmp_path):
    ctx=context(tmp_path)
    (tmp_path/'paper.md').write_text('正文：效率0.95\n已保存结果。')
    (tmp_path/'attachment').mkdir(exist_ok=True);(tmp_path/'attachment/input.txt').write_text('原始数据6')
    (tmp_path/'.env').write_text('must-never-appear')
    async def run():
        listed=await invoke(ctx,'research.list_files',limit=1)
        assert listed['next_offset']==1
        following=await invoke(ctx,'research.list_files',offset=1,limit=10)
        assert {r['path']for r in listed['files']+following['files']}=={'paper.md','attachment/input.txt'}
        found=await invoke(ctx,'research.search_files',query='效率0.95',pattern='*.md')
        assert len(found['matches'])==1 and found['matches'][0]['path']=='paper.md'
        read=await invoke(ctx,'research.read_artifact',path='paper.md',limit=4)
        assert read['next_start']==4 and read['version']==found['matches'][0]['version']
        (tmp_path/'paper.md').write_text('修正后的正文')
        changed=await invoke(ctx,'research.read_artifact',path='paper.md')
        assert changed['version']!=read['version']
        for name in ['../paper.md','.env','/etc/passwd']:
            with pytest.raises(ValueError):await invoke(ctx,'research.read_artifact',path=name)
        assert not ctx.kernels._kernels
    asyncio.run(run())


@pytest.mark.usefixtures('offline_kernel')
def test_natural_final_reply_uses_same_publication_without_recomputation(tmp_path):
    from ai_system.graph.agent import PaperGraphAgent
    from helpers.graph import FakeModel,TASK
    class NaturalFinal(FakeModel):
        def _research_step(self,messages,plan):
            reply=super()._research_step(messages,plan)
            if reply.tool_calls and reply.tool_calls[0]['name']=='research_deliver':
                return AIMessage(content='已完成计算与正文，结果与局限见文件。')
            return reply
    agent=PaperGraphAgent(NaturalFinal(),workspace_dir=str(tmp_path))
    assert 'paper.md' in asyncio.run(agent.run(TASK))
    state=ResearchStore(tmp_path,agent._load_thread()).snapshot()
    assert state['outcome']=='completed' and not state['experiments']
    assert len(WorkspaceFS(str(tmp_path))._manifest['runs'])==4


def test_final_reply_can_deliver_without_answer_metadata_but_waits_for_files(tmp_path):
    from ai_system.research.loop import prepare_action
    ctx=context(tmp_path,ResearchToolsModel(respond=lambda _:AIMessage(content='完成了。')))
    (tmp_path/'paper.md').write_text('# 当前稿件，未完成部分已说明')
    assert not asyncio.run(prepare_action(ctx))
    assert ctx.research.snapshot()['publication']['stage']=='export'
    async def pending():
        q=await invoke(ctx,'research.create_question',question='结果是多少？')
        rid=ctx.fs.create_run();ctx.fs.get_run_info(rid).update(scope='research',research_id=ctx.research.research_id,status='success',delivery_error='not downloaded')
        with pytest.raises(ValueError,match='尚未确认'):
            await invoke(ctx,'research.deliver',reason='准备交付')
        assert ctx.research.snapshot()['publication'] is None
    # A new instruction can repair the interrupted decision without replaying it.
    with ctx.research._transaction() as db:
        state=ctx.research._read(db);state['action']=None;state['publication']=None;ctx.research._save(db,state,'test_recovery',{})
    asyncio.run(pending())


def test_historical_unknown_execution_does_not_block_current_delivery(tmp_path):
    ctx=context(tmp_path,ResearchToolsModel(respond=lambda _:AIMessage(content='完成。')))
    (tmp_path/'paper.md').write_text('# 当前结果\n结果为6；早期初始化中断未作为结果依据。')
    async def run():
        q=await invoke(ctx,'research.create_question',question='结果是多少？')
        rid=ctx.fs.create_run()
        old=ctx.fs.get_run_info(rid)
        old.update(scope='research',research_id=ctx.research.research_id,status='error',
                   failure_kind='transport_unknown',error='早期初始化未确认，未采用其结果')
        await invoke(ctx,'research.deliver',reason='当前结果和正文已完成，保留早期失败记录。')
        assert ctx.research.snapshot()['publication']['stage']=='export'
        assert old['status']=='error' and old['failure_kind']=='transport_unknown'
        assert list(ctx.fs._manifest['runs'])==[rid]
    asyncio.run(run())
