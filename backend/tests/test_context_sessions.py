"""Continuity, context isolation and manuscript ownership through real boundaries."""
import asyncio,json,hashlib
from types import SimpleNamespace
from pathlib import Path
import pytest
from langchain_core.messages import AIMessage,HumanMessage,SystemMessage,ToolMessage
from helpers.context import session_context as context, ContextRequest as Request
from research_tools_helpers import ResearchToolsModel,tool


def test_manuscript_edits_preserve_external_changes_and_use_no_writer(tmp_path):
    from ai_system.modules.registry import invoke
    from ai_system.research.models import ResearchConflict
    ctx=context(tmp_path)
    async def run():
        first=await invoke(ctx,'writing.edit',content='# 正文\n效率0.95。')
        (tmp_path/'paper.md').write_text('# 人工修改\n效率0.95。')
        with pytest.raises(ResearchConflict):
            await invoke(ctx,'writing.edit',content='不要覆盖人工修改',expected_version=first['version'])
        assert (tmp_path/'paper.md').read_text()=='# 人工修改\n效率0.95。'
        current=hashlib.sha256((tmp_path/'paper.md').read_bytes()).hexdigest()
        await invoke(ctx,'writing.edit',content='效率0.95，边界已核对。',old_text='效率0.95。',expected_version=current)
        assert (tmp_path/'paper.md').read_text()=='# 人工修改\n效率0.95，边界已核对。'
    asyncio.run(run())


def test_invalid_manuscript_target_does_not_close_the_computation_kernel(tmp_path):
    from ai_system.modules.registry import invoke
    from unittest.mock import AsyncMock
    ctx=context(tmp_path)
    ctx.kernels.close=AsyncMock()
    with pytest.raises(ValueError):
        asyncio.run(invoke(ctx,'writing.edit',path='solve.py',content='print(1)'))
    ctx.kernels.close.assert_not_awaited()
    assert not (tmp_path/'solve.py').exists()


@pytest.mark.usefixtures('offline_kernel')
def test_missing_response_reconciles_committed_code_instead_of_replaying(tmp_path):
    from ai_system.modules.registry import invoke
    from ai_system.research.session import invoke_once
    from ai_system.research.runtime import execution_scope
    ctx=context(tmp_path)
    async def run():
        q=await invoke(ctx,'research.update_question',question='计算一次并保存')
        args={'question_id':q['question_id'],'code':"from pathlib import Path\np=Path('outputs/count.txt')\np.write_text(str(int(p.read_text())+1 if p.exists() else 1))\nprint(p.read_text())"}
        with execution_scope(action_id='stable-call'):
            await invoke_once(ctx,'research.run_python',args,'stable-call')
        with ctx.research._transaction() as db:
            db.execute("DELETE FROM commands WHERE id='tool-response:stable-call'")
        with execution_scope(action_id='stable-call'):
            value=await invoke_once(ctx,'research.run_python',args,'stable-call')
        assert value['recovered'] and len(ctx.fs._manifest['runs'])==1 and not ctx.research.snapshot()['experiments']
        assert (tmp_path/'outputs/count.txt').read_text()=='1'
        await ctx.kernels.close()
    asyncio.run(run())


@pytest.mark.usefixtures('offline_kernel')
@pytest.mark.parametrize('mode',['isolated','fork'])
def test_child_has_scoped_files_persistent_followups_and_archived_runs(tmp_path,mode):
    from ai_system.modules.registry import invoke
    seen=[]
    def child(messages):
        seen.append(messages)
        rows=[m for m in messages if isinstance(m,ToolMessage)]
        if not rows:return tool('run_python',{'code':"from pathlib import Path\nassert Path('attachment/assigned.txt').read_text()=='6'\nassert not Path('attachment/unassigned.txt').exists()\nPath('outputs/result.txt').write_text('12')\nprint(12)"},'child-code')
        if len(rows)==1:return tool('read_file',{'path':'outputs/result.txt'},'read-result')
        assert '12' in rows[1].content and rows[1].status=='success'
        return AIMessage(content='计算结果12；结果文件outputs/result.txt；已检查输入。')
    model=ResearchToolsModel(respond=child);ctx=context(tmp_path,model)
    folder=tmp_path/'attachment';folder.mkdir(exist_ok=True)
    (folder/'assigned.txt').write_text('6');(folder/'unassigned.txt').write_text('not assigned')
    ctx.agent_messages=[HumanMessage(content='parent-context-marker')]
    async def run():
        result=await invoke(ctx,'research.delegate',task='独立计算',context_mode=mode,files=['attachment/assigned.txt'])
        assert result['status']=='completed' and len(result['run_ids'])==1
        activities=ctx.research.snapshot()['activity']
        parent=next(row for row in activities if row['capability']=='research.delegate')
        child=[row for row in activities if row['capability']=='run_python']
        assert len(child)==1 and child[0]['id']!=parent['id']
        assert child[0]['parent_call_id']==parent['id'], 'independent execution keeps its own record'
        assert bool(any('parent-context-marker' in str(m.content) for m in seen[0]))==(mode=='fork')
        actual=[p for p in result['files'] if p.endswith('/outputs/result.txt')]
        assert len(actual)==1 and ctx.fs.canonical_artifact(actual[0]).read_text()=='12'
        assert ctx.fs.get_run_info(result['run_ids'][0])['status']=='success'
        following=await invoke(ctx,'research.delegate',task_id=result['task_id'],task='解释上次结果')
        assert following['run_ids']==result['run_ids']
        assert any(getattr(m,'tool_call_id',None)=='child-code' for m in seen[-1])
        (folder/'assigned.txt').write_text('changed')
        with pytest.raises(ValueError,match='输入已变化'):
            await invoke(ctx,'research.delegate',task_id=result['task_id'],task='再次解释')
    asyncio.run(run())


def test_token_compaction_keeps_requirements_recent_tools_and_reuses_summary(tmp_path):
    from ai_system.graph.compaction import ContextMiddleware
    prompts=[]
    def summarize(messages):
        prompts.append(messages)
        assert 'private-reasoning-marker' not in str(messages)
        return AIMessage(content=json.dumps({'notes':['效率0.95；修正了递推实现。']}))
    model=ResearchToolsModel(respond=summarize,profile={'max_input_tokens':7000})
    ctx=context(tmp_path,model);middleware=ContextMiddleware(ctx,model,'compression')
    messages=[SystemMessage(content='保持参数和未知。'),HumanMessage(content='hard efficiency=0.95')]
    for i in range(9):
        messages += [tool('run_python',{'code':'long_code = '+repr('校验'*700)},str(i)),ToolMessage(content='真实输出'+str(i),tool_call_id=str(i),name='run_python')]
        messages[-2].additional_kwargs['reasoning_content']='private-reasoning-marker'
    sent=[]
    async def handler(request):sent.append(request.messages);return None
    async def run():
        await middleware.awrap_model_call(Request(messages),handler)
        await middleware.awrap_model_call(Request(messages),handler)
    asyncio.run(run())
    assert model.calls==1 and len(sent[0])<len(messages)
    assert sent[0][1].content=='hard efficiency=0.95'
    assert sent[0][-1].tool_call_id=='8' and sent[0][-2].tool_calls[0]['id']=='8'
    assert any(json.loads(m.content).get('source',{}).get('requirement')==ctx.research.snapshot()['spec']['requirement'] for m in sent[0] if isinstance(m,HumanMessage) and str(m.content).startswith('{'))
    assert '历史方法笔记' in sent[0][2].content
    assert len(messages)==20  # Saved original history is never destructively trimmed.


@pytest.mark.usefixtures('offline_kernel')
def test_child_delivery_retry_does_not_repeat_model_or_calculation(tmp_path,monkeypatch):
    from ai_system.modules.registry import invoke
    from ai_system.sandbox import artifacts
    original=artifacts.capture_run_files
    failed=False
    def publish(source,target,*args,**kwargs):
        nonlocal failed
        # Fail only the child-to-parent handoff, after its local archival succeeds.
        if source.root!=target.root and not failed:
            failed=True;raise ConnectionError('controlled delivery interruption')
        return original(source,target,*args,**kwargs)
    monkeypatch.setattr(artifacts,'capture_run_files',publish)
    def child(messages):
        if not any(isinstance(m,ToolMessage) for m in messages):
            return tool('run_python',{'code':"from pathlib import Path\nPath('outputs/value.txt').write_text('6')\nprint(6)"},'code-once')
        return AIMessage(content='结果6，已保存。')
    model=ResearchToolsModel(respond=child);ctx=context(tmp_path,model)
    async def run():
        with pytest.raises(ConnectionError):await invoke(ctx,'research.delegate',task='保存一次结果')
        calls=model.calls
        result=await invoke(ctx,'research.delegate',task='保存一次结果')
        assert model.calls==calls and len(result['run_ids'])==1 and result['status']=='completed'
        assert result['files']
    asyncio.run(run())


def test_manuscript_validation_needs_no_calculation_experiment(tmp_path):
    from ai_system.modules.registry import invoke
    from ai_system.modules.writing.validation import check_sections
    from ai_system.research.control import graph_view
    ctx=context(tmp_path)
    async def run():
        await invoke(ctx,'writing.edit',content='# 文献讨论\n![missing](outputs/missing.png)')
        issues=check_sections(graph_view(ctx.research.snapshot(),root=ctx.workspace),ctx)
        assert len(issues)==1 and '图片文件不可用' in issues[0]['message']
        assert not ctx.research.snapshot()['experiments']
    asyncio.run(run())


@pytest.mark.usefixtures('offline_kernel')
def test_child_prose_cannot_turn_failed_computation_into_success(tmp_path):
    from ai_system.modules.registry import capabilities,CapabilityRequest
    def child(messages):
        if not any(isinstance(m,ToolMessage) for m in messages):
            return tool('run_python',{'code':"raise ValueError('controlled failed check')"},'failed-code')
        return AIMessage(content='错误地声称校验通过。')
    ctx=context(tmp_path,ResearchToolsModel(respond=child))
    async def run():
        result=await capabilities().invoke(CapabilityRequest(name='research.delegate',inputs={'task':'计算并校验'}),ctx)
        assert result.execution_status=='failed'
        assert result.value['execution_status']=='failed' and result.value['runs'][0]['status']=='error'
        assert not result.value['files'] and '不构成执行成功' in result.value['execution_warning']
    asyncio.run(run())
