"""Forks preserve provider input and enforce child capabilities at execution."""
import asyncio
import json
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict, messages_from_dict
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from ai_system.agent.fork import PREAMBLE
from ai_system.agent.store import AgentStore
from ai_system.modules.registry import invoke
from ai_system.research.session import run_research
from helpers.context import session_context as context
from research_tools_helpers import ResearchToolsModel, tool


class RecordingModel(ResearchToolsModel):
    requests: list = Field(default_factory=list,exclude=True)
    openai_api_base: str | None = None

    def bind_tools(self,tools,**kwargs):
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools])

    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self.requests.append({'messages':deepcopy(messages_to_dict(messages)), 'tools':deepcopy(kwargs.get('tools',[]))})
        return super()._generate(messages,stop,run_manager,**kwargs)


def chat_payload(row):
    from langchain_openai import ChatOpenAI
    model=ChatOpenAI(model='unit-test',api_key='offline',use_responses_api=False)
    return model._get_request_payload(messages_from_dict(row['messages']),tools=row['tools'])


@pytest.mark.usefixtures('offline_kernel')
def test_fork_preserves_effective_request_and_rebinds_permissions_files_and_runs(tmp_path):
    from ai_system.research.planner import PROMPT
    blocked=['writing_edit','research_update_plan','research_delegate','research_ask_user','research_deliver']
    archive=None
    def respond(messages):
        nonlocal archive
        fork=any(isinstance(m,HumanMessage) and str(m.content).startswith(PREAMBLE) for m in messages)
        rows=[m for m in messages if isinstance(m,ToolMessage)]
        if not fork:
            if not rows:return tool('research_read_artifact',{'path':'attachment/large.txt','limit':24000},'parent-read')
            if len(rows)==1:
                archive=json.loads(rows[0].content)['_full_output_path']
                return tool('research_delegate',{'context_mode':'fork','task':'在既定参数下计算并保存12',
                    'files':['attachment/assigned.txt','config/parameters.json']},'delegate')
            assert json.loads(rows[-1].content)['answer']=='结果12，已保存。'
            return AIMessage(content='子任务结果已核对。')
        own=[m for m in rows if m.tool_call_id!='parent-read']
        if not own:
            args=[{'content':'must not overwrite'}, {'steps':[{'title':'must not update','status':'in_progress'}]},
                  {'task':'must not recurse'}, {'message':'must not ask'}, {'title':'must not deliver'}]
            return AIMessage(content='',tool_calls=[{'name':name,'args':arg,'id':name} for name,arg in zip(blocked,args)])
        for row in own[:5]:
            assert row.status=='error' and 'fork子任务不能执行' in row.content
        if len(own)==5:return tool('research_read_artifact',{'path':archive},'archive')
        if len(own)==6:
            assert '历史材料' in json.loads(own[-1].content)['excerpt']
            return tool('workspace_edit',{'path':'code/solve.py','content':
                "from pathlib import Path\nassert not Path('attachment/unassigned.txt').exists()\n"
                "assert Path('config/parameters.json').read_text()=='{\"factor\":2}'\n"
                "value=int(Path('attachment/assigned.txt').read_text())*2\n"
                "Path('outputs/value.txt').write_text(str(value))\nprint(value)"},'source')
        if len(own)==7:return tool('research_run_file',{'path':'code/solve.py'},'calculate')
        if len(own)==8:
            assert json.loads(own[-1].content)['execution_status']=='succeeded'
            assert (tmp_path/'paper.md').read_text()=='# Main manuscript'
            assert not (tmp_path/'code/solve.py').exists()
            assert ctx.research.snapshot().get('progress_plan') is None
            return AIMessage(content='结果12，已保存。')
        # A restarted caller continues the child conversation and reads saved output.
        assert any(m.tool_call_id=='calculate' for m in own)
        return AIMessage(content='结果12，已保存。')
    model=RecordingModel(respond=respond)
    ctx=context(tmp_path,model)
    ctx.coder_llm=ResearchToolsModel(respond=lambda _:pytest.fail('Fork must use the parent model'))
    (tmp_path/'paper.md').write_text('# Main manuscript')
    (tmp_path/'attachment').mkdir(exist_ok=True)
    (tmp_path/'attachment/large.txt').write_text('历史材料'*5000)
    (tmp_path/'attachment/assigned.txt').write_text('6')
    (tmp_path/'attachment/unassigned.txt').write_text('not assigned')
    ctx.fs.write_text('config/parameters.json','{"factor":2}')
    async def run():
        await run_research(ctx)
        parent,child=model.requests[1:3]
        assert child['tools']==parent['tools']
        assert child['messages'][:-1]==parent['messages']
        assert PROMPT in child['messages'][0]['data']['content']
        assert child['messages'][-1]['data']['content'].startswith(PREAMBLE)
        assert '历史材料'*5000 not in str(child), 'Reuse projected context rather than the full canonical history'
        result=json.loads(next(m.content for m in AgentStore(tmp_path).messages('sessions-lead') if isinstance(m,ToolMessage) and m.name=='research_delegate'))
        assert result['status']=='completed' and len(result['run_ids'])==1
        assert 'fork_request' not in result
        paths=[p for p in result['files'] if p.endswith('/outputs/value.txt')]
        assert len(paths)==1 and ctx.fs.canonical_artifact(paths[0]).read_text()=='12'
        assert ctx.fs.get_run_info(result['run_ids'][0])['scope']=='delegated'
        # Fresh GraphContext, with no live request/model snapshot, simulates restart.
        restarted=context(tmp_path,model)
        following=await invoke(restarted,'research.delegate',task_id=result['task_id'],task='继续解释，不要重算')
        assert following['context_mode']=='fork' and following['run_ids']==result['run_ids']
        assert model.requests[-1]['tools']==child['tools']
        assert model.requests[-1]['messages'][:len(parent['messages'])]==parent['messages']
        (tmp_path/'attachment/assigned.txt').write_text('changed')
        with pytest.raises(ValueError,match='输入已变化'):
            await invoke(restarted,'research.delegate',task_id=result['task_id'],task='再次计算')
    asyncio.run(run())


def test_fork_without_effective_parent_request_fails_instead_of_changing_prefix(tmp_path):
    ctx=context(tmp_path,ResearchToolsModel(respond=lambda _:pytest.fail('No model request without a parent prefix')))
    ctx.agent_messages=[HumanMessage(content='Only raw history is available')]
    with pytest.raises(ValueError,match='前缀快照'):
        asyncio.run(invoke(ctx,'research.delegate',context_mode='fork',task='不能假装继承完整请求'))


@pytest.mark.parametrize('native',['writing.edit','research.update_plan','research.delegate','research.deliver','research.ask_user'])
def test_fork_permissions_also_guard_direct_registry_calls(tmp_path,native):
    from ai_system.agent.fork import ALLOWED_CAPABILITIES
    from ai_system.modules.registry import capabilities
    ctx=context(tmp_path);ctx.allowed_capabilities=ALLOWED_CAPABILITIES
    with pytest.raises(ValueError,match='fork子任务不能执行'):
        asyncio.run(capabilities().execute(native,{},ctx))


def test_fork_inherits_compacted_history_instead_of_summarizing_original_again(tmp_path):
    from langchain_core.tools import StructuredTool
    from ai_system.agent.loop import AgentLoop,run_session
    from ai_system.modules.delegation import DelegateInput
    def respond(messages):
        if '摘录继续研究' in str(messages[0].content):return AIMessage(content='{"notes":["已核对效率0.95，保留方法选择。"]}')
        if any(str(m.content).startswith(PREAMBLE) for m in messages):return AIMessage(content='子任务完成')
        if any(isinstance(m,ToolMessage) and m.name=='research_delegate' for m in messages):return AIMessage(content='父任务继续')
        return tool('research_delegate',{'task':'沿用方法继续调查','context_mode':'fork'},'fork')
    model=RecordingModel(respond=respond,profile={'max_input_tokens':8000});ctx=context(tmp_path,model)
    async def delegate(**kwargs):
        from ai_system.modules.registry import capabilities
        return await capabilities().execute('research.delegate',kwargs,ctx)
    tools=[StructuredTool(name='research_delegate',description='委派子任务',args_schema=DelegateInput,coroutine=delegate)]
    history=[HumanMessage(content='效率0.95，不要丢失单位')]
    for i in range(10):
        history += [tool('run_python',{'code':'数值校验'*300},str(i)),ToolMessage('实际输出'+str(i),tool_call_id=str(i))]
    async def run():
        await run_session(AgentLoop(model,tools,'共同研究规则',ctx=ctx), 'sessions-lead',history,ctx=ctx)
    asyncio.run(run())
    requests=[row for row in model.requests if row['tools']]
    parent,child=requests[:2]
    assert '历史方法笔记' in str(parent) and len(parent['messages'])<len(history)
    parent_wire,child_wire=chat_payload(parent),chat_payload(child)
    assert child_wire['tools']==parent_wire['tools'] and child_wire['messages'][:-1]==parent_wire['messages']
    assert len([row for row in model.requests if not row['tools']])==1


@pytest.mark.parametrize('placement',['inline','deepseek'])
def test_fork_preserves_hydrated_images_without_persisting_image_bytes(tmp_path,placement):
    from PIL import Image
    from langchain_core.tools import StructuredTool
    from ai_system.agent.images import page_ref
    from ai_system.agent.loop import AgentLoop,run_session
    from ai_system.modules.delegation import DelegateInput
    def respond(messages):
        if any(str(m.content).startswith(PREAMBLE) for m in messages):return AIMessage(content='已检查继承页面')
        if any(isinstance(m,ToolMessage) and m.name=='research_delegate' for m in messages):return AIMessage(content='完成')
        return tool('research_delegate',{'task':'核对前面看过的图','context_mode':'fork'},'fork-image')
    model=RecordingModel(respond=respond,openai_api_base='https://api.deepseek.com' if placement=='deepseek' else None)
    ctx=context(tmp_path,model)
    path='.system/paper-project/previews/'+'a'*64+'/1.png'
    image=ctx.fs.abs(path);image.parent.mkdir(parents=True,exist_ok=True)
    Image.new('RGB',(20,20),'white').save(image)
    ref=page_ref(tmp_path,{'path':path,'page':1})
    history=[HumanMessage(content='检查这一页'),tool('read_file',{'path':'paper.pdf'},'page'),
             ToolMessage(json.dumps({'_images':[ref]}),tool_call_id='page',name='read_file')]
    async def delegate(**kwargs):
        from ai_system.modules.registry import capabilities
        return await capabilities().execute('research.delegate',kwargs,ctx)
    tools=[StructuredTool(name='research_delegate',description='委派子任务',args_schema=DelegateInput,coroutine=delegate)]
    asyncio.run(run_session(AgentLoop(model,tools,'共同规则',ctx=ctx),'sessions-lead',history,ctx=ctx))
    parent,child=model.requests[:2]
    assert 'data:image/png;base64,' in str(parent)
    parent_wire,child_wire=chat_payload(parent),chat_payload(child)
    assert child_wire['messages'][:-1]==parent_wire['messages'] and child_wire['tools']==parent_wire['tools']
    stored=AgentStore(tmp_path).request('sessions-lead')
    assert 'data:image/' not in json.dumps(stored)


@pytest.mark.parametrize('provider',['chat','responses','anthropic'])
def test_fork_matches_parent_request_through_real_provider_serializers(tmp_path,provider):
    import httpx
    import httpx2
    from langchain_anthropic import ChatAnthropic
    from ai_system.core_handlers.llm_providers import MeteredChatOpenAI
    requests=[]
    args={'task':'只回答当前子任务','context_mode':'fork'}
    def handle(request):
        requests.append(json.loads(request.content))
        first=len(requests)==1
        if provider=='anthropic':
            content=[{'type':'tool_use','id':'delegate','name':'research_delegate','input':args}] if first else [{'type':'text','text':'完成'}]
            body={'id':'msg_fake','type':'message','role':'assistant','model':'claude-sonnet-4-5',
                  'content':content,'stop_reason':'tool_use' if first else 'end_turn','stop_sequence':None,
                  'usage':{'input_tokens':100,'output_tokens':10}}
        elif provider=='responses':
            output=[{'id':'fc_fake','type':'function_call','call_id':'delegate','name':'research_delegate',
                     'arguments':json.dumps(args),'status':'completed'}] if first else [
                     {'id':'msg_fake','type':'message','role':'assistant','status':'completed',
                      'content':[{'type':'output_text','text':'完成','annotations':[]}]}]
            body={'id':'resp_fake','object':'response','created_at':0,'status':'completed','model':'gpt-6.1-terra','output':output,
                  'usage':{'input_tokens':100,'output_tokens':10,'total_tokens':110}}
        else:
            message={'role':'assistant','content':None,'tool_calls':[{'id':'delegate','type':'function',
                'function':{'name':'research_delegate','arguments':json.dumps(args)}}]} if first else {'role':'assistant','content':'完成'}
            body={'id':'chat_fake','object':'chat.completion','created':0,'model':'unit-test',
                  'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if first else 'stop'}],
                  'usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110}}
        return (httpx2 if provider=='anthropic' else httpx).Response(200,json=body)
    async def run():
        transport_api=httpx2 if provider=='anthropic' else httpx
        async with transport_api.AsyncClient(transport=transport_api.MockTransport(handle)) as transport:
            if provider=='anthropic':
                model=ChatAnthropic(model='claude-sonnet-4-5',api_key='offline',streaming=False,max_tokens=1024)
                model._async_client=model._async_client.with_options(http_client=transport)
            else:
                model=MeteredChatOpenAI(model='gpt-6.1-terra' if provider=='responses' else 'unit-test',
                    api_key='offline',base_url='https://offline.invalid/v1',streaming=False,max_tokens=1024,
                    use_responses_api=provider=='responses',http_async_client=transport)
            ctx=context(tmp_path,model)
            ctx.coder_llm=ResearchToolsModel(respond=lambda _:pytest.fail('Fork changed the model'))
            (tmp_path/'paper.md').write_text('# 已有稿件')
            await run_research(ctx)
    asyncio.run(run())
    assert len(requests)==3
    parent,child=requests[:2]
    assert child['tools']==parent['tools'] and child['model']==parent['model']
    if provider=='anthropic':
        assert child['system']==parent['system']
        assert child['messages'][:-1]==parent['messages'][:-1]
        assert child['messages'][-1]['content'][:-1]==parent['messages'][-1]['content']
        assert child['messages'][-1]['content'][-1]['text'].startswith(PREAMBLE)
    else:
        key='input' if provider=='responses' else 'messages'
        assert child[key][:-1]==parent[key]
        assert child[key][-1]['content'].startswith(PREAMBLE)


@pytest.mark.usefixtures('offline_kernel')
def test_interrupted_fork_recovers_durable_prefix_and_committed_tool_without_replaying(tmp_path,monkeypatch):
    failed=False
    original=AgentStore.append_message
    def append(store,session,message):
        nonlocal failed
        if not failed and '-task-' in session and isinstance(message,ToolMessage) and message.name=='research_run_python':
            failed=True
            raise ConnectionError('controlled interruption after tool commit')
        return original(store,session,message)
    monkeypatch.setattr(AgentStore,'append_message',append)
    def respond(messages):
        fork=any(str(m.content).startswith(PREAMBLE) for m in messages)
        if fork:
            if not any(isinstance(m,ToolMessage) and m.name=='research_run_python' for m in messages):
                return tool('research_run_python',{'code':"from pathlib import Path\np=Path('outputs/count.txt')\np.write_text(str(int(p.read_text())+1 if p.exists() else 1))\nprint(p.read_text())"},'once')
            return AIMessage(content='只计算一次，结果1')
        if any(isinstance(m,ToolMessage) and m.name=='research_delegate' for m in messages):return AIMessage(content='完成')
        return tool('research_delegate',{'task':'只执行一次并保存','context_mode':'fork'},'delegate-once')
    model=RecordingModel(respond=respond);ctx=context(tmp_path,model)
    (tmp_path/'paper.md').write_text('# 已有稿件')
    async def run():
        with pytest.raises(ConnectionError,match='controlled interruption'):
            await run_research(ctx)
        parent_prefix=model.requests[0]
        restarted=context(tmp_path,model)
        await run_research(restarted)
        rows=[m for m in AgentStore(tmp_path).messages('sessions-lead') if isinstance(m,ToolMessage) and m.name=='research_delegate']
        result=json.loads(rows[-1].content)
        assert result['answer']=='只计算一次，结果1' and len(result['run_ids'])==1
        saved=next(p for p in result['files'] if p.endswith('/outputs/count.txt'))
        assert restarted.fs.canonical_artifact(saved).read_text()=='1'
        child_resumed=model.requests[-2]
        assert child_resumed['messages'][:len(parent_prefix['messages'])]==parent_prefix['messages']
    asyncio.run(run())
