"""Model edits use an observed receipt; humans' concurrent edits remain protected."""
import asyncio,json
import pytest
from langchain_core.messages import AIMessage,ToolMessage
from ai_system.modules.registry import invoke
from ai_system.modules.writing.draft import DraftEdit,observed_version
from ai_system.research.loop import prepare_action,resume
from ai_system.research.runtime import ModelServiceError
from test_context_sessions import context
from research_tools_helpers import ResearchToolsModel,tool


@pytest.mark.parametrize('newline',['\n','\r\n'])
def test_edit_uses_read_receipt_even_when_model_miscopies_hash(tmp_path,newline):
    (tmp_path/'paper.md').write_bytes(('# 原稿'+newline+'预测13。').encode())
    seen=[]
    def respond(messages):
        seen.append(messages);results=[m for m in messages if isinstance(m,ToolMessage)]
        if not results:return tool('research_read_artifact',{'path':'paper.md'},'read')
        if len(results)==1:
            assert 'expected_version' not in DraftEdit.model_json_schema()['properties']
            return tool('writing_edit',{'content':'预测15。','old_text':'预测13。','expected_version':'copied-incorrectly'},'edit')
        assert results[-1].status=='success'
        return tool('research_pause',{'message':'修改已保存。'},'pause')
    ctx=context(tmp_path,ResearchToolsModel(respond=respond))
    asyncio.run(prepare_action(ctx))
    assert (tmp_path/'paper.md').read_text()=='# 原稿\n预测15。'
    assert len(seen)==3


def test_model_cannot_overwrite_unread_file_using_a_guessed_current_hash(tmp_path):
    import hashlib
    path=tmp_path/'paper.md';path.write_text('人工稿件')
    count=0
    def respond(messages):
        nonlocal count
        count+=1
        if count==1:return tool('writing_edit',{'content':'覆盖','expected_version':hashlib.sha256(path.read_bytes()).hexdigest()},'unread')
        last=next(m for m in reversed(messages) if isinstance(m,ToolMessage))
        assert last.status=='error' and '先读取' in last.content
        return tool('research_pause',{'message':'需要先读稿件。'},'pause')
    ctx=context(tmp_path,ResearchToolsModel(respond=respond));asyncio.run(prepare_action(ctx))
    assert path.read_text()=='人工稿件'


def test_edit_conflicts_with_human_change_after_observation(tmp_path):
    path=tmp_path/'paper.md';path.write_text('原稿13')
    count=0
    def respond(messages):
        nonlocal count
        count+=1
        if count==1:return tool('research_read_artifact',{'path':'paper.md'},'read')
        if count==2:
            path.write_text('人工更新14')
            return tool('writing_edit',{'content':'模型修改15'},'stale')
        last=next(m for m in reversed(messages) if isinstance(m,ToolMessage))
        assert last.status=='error' and '发生变化' in last.content
        return tool('research_pause',{'message':'保留人工更新。'},'pause')
    ctx=context(tmp_path,ResearchToolsModel(respond=respond));asyncio.run(prepare_action(ctx))
    assert path.read_text()=='人工更新14'


def test_observed_version_survives_a_new_agent_context(tmp_path):
    path=tmp_path/'paper.md';path.write_text('原稿13')
    def first(messages):
        if any(isinstance(m,ToolMessage) for m in messages):raise ModelServiceError('controlled interruption after read')
        return tool('research_read_artifact',{'path':'paper.md'},'read-before-interruption')
    original=context(tmp_path,ResearchToolsModel(respond=first));asyncio.run(prepare_action(original))
    count=0
    def continued(messages):
        nonlocal count
        count+=1
        if count==1:return tool('writing_edit',{'content':'更新15'},'after-restart')
        return tool('research_pause',{'message':'恢复完成，未重读或重算。'},'pause')
    restored=context(tmp_path,ResearchToolsModel(respond=continued))
    resume(restored.research);asyncio.run(prepare_action(restored))
    assert path.read_text()=='更新15' and count==2, (count,restored.research.snapshot().get('action'))


def test_new_read_and_edit_in_same_turn_cannot_accept_unseen_human_changes(tmp_path):
    path=tmp_path/'paper.md';path.write_text('原稿13')
    count=0
    def respond(messages):
        nonlocal count
        count+=1
        if count==1:return tool('research_read_artifact',{'path':'paper.md'},'first-read')
        if count==2:
            path.write_text('人工更新14')
            return AIMessage(content='',tool_calls=[
                {'id':'parallel-read','name':'research_read_artifact','args':{'path':'paper.md'}},
                {'id':'parallel-edit','name':'writing_edit','args':{'content':'模型覆盖15'}}])
        edits=[m for m in messages if isinstance(m,ToolMessage) and m.tool_call_id=='parallel-edit']
        assert edits[-1].status=='error'
        return tool('research_pause',{'message':'先处理人工修改。'},'pause')
    ctx=context(tmp_path,ResearchToolsModel(respond=respond));asyncio.run(prepare_action(ctx))
    assert path.read_text()=='人工更新14'
