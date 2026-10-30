"""Cache reuse must preserve visible facts, receipts, reasoning and recovery."""
import asyncio
import json
from langchain_core.messages import AIMessage,HumanMessage,ToolMessage,messages_to_dict
from ai_system.graph.compaction import ContextMiddleware
from ai_system.research.runtime import execution_links,ResearchRuntime,execution_scope
from ai_system.research.views import model_usage
from test_context_delivery import context,Request
from research_tools_helpers import ResearchToolsModel,tool


def test_projection_is_append_only_after_offload_and_process_restart(tmp_path):
    model=ResearchToolsModel(respond=lambda _:None)
    ctx=context(tmp_path,model)
    history=[HumanMessage('source efficiency=0.95'),tool('read_file',{'path':'input.txt'},'read'),
             ToolMessage('真实资料'*2000,tool_call_id='read')]
    history[1].additional_kwargs['reasoning_content']='required-thinking-content'
    sent=[];stats=[]
    async def handle(request):
        sent.append(messages_to_dict(request.messages));stats.append(execution_links()['context_usage'])
    async def run():
        await ContextMiddleware(ctx,model,'stable').awrap_model_call(Request(history),handle)
        history.extend([tool('read_file',{'path':'next.txt'},'next'),ToolMessage('new observation',tool_call_id='next')])
        # Reload both middleware and durable projection, not just an in-memory list.
        await ContextMiddleware(ctx,model,'stable').awrap_model_call(Request(history),handle)
    asyncio.run(run())
    assert sent[1][:len(sent[0])]==sent[0]
    assert stats[0]['cache_phase']=='cold' and stats[1]['cache_phase']=='stable'
    assert stats[1]['reused_prefix_messages']==len(sent[0])
    assert stats[0]['offloaded_observations']==stats[1]['offloaded_observations']==1
    assert 'required-thinking-content' in str(sent[1]) and history[2].content=='真实资料'*2000


def test_compaction_starts_one_new_prefix_then_appends(tmp_path):
    model=ResearchToolsModel(respond=lambda _:AIMessage('{"notes":["保持效率0.95。"]}'),profile={'max_input_tokens':10000})
    ctx=context(tmp_path,model);history=[HumanMessage('original')];sent=[];stats=[]
    async def handle(r):sent.append(messages_to_dict(r.messages));stats.append(execution_links()['context_usage'])
    async def run():
        middleware=ContextMiddleware(ctx,model,'epochs')
        await middleware.awrap_model_call(Request(history),handle)
        for i in range(12):
            history.extend([tool('run_python',{'code':'计算'*500},str(i)),ToolMessage(str(i),tool_call_id=str(i))])
        await middleware.awrap_model_call(Request(history),handle)
        history.extend([tool('read_file',{'path':'result.csv'},'last'),ToolMessage('correct',tool_call_id='last')])
        await ContextMiddleware(ctx,model,'epochs').awrap_model_call(Request(history),handle)
    asyncio.run(run())
    assert model.calls==1
    assert [s['cache_phase'] for s in stats]==['cold','after_compaction','stable']
    assert sent[2][:len(sent[1])]==sent[1]
    assert stats[1]['total_estimated_tokens']<=stats[1]['low_watermark']


def test_cache_breakdown_does_not_treat_missing_counts_as_misses(tmp_path):
    ctx=context(tmp_path);meter=ResearchRuntime(tmp_path,ctx.research.research_id)
    from ai_system.graph.tracing import labelled
    for label,phase,hits in [('observe','cold',0),('observe','stable',80),('context_summary','stable',0),('task:child','cold',None)]:
        with labelled(label),execution_scope(context_usage={'cache_phase':phase}):
            sequence=meter.reserve('offline')
        usage={'input_tokens':100,'output_tokens':10}
        if hits is not None:usage['input_token_details']={'cache_read':hits}
        meter.usage(sequence,usage)
    with labelled('observe'),execution_scope(context_usage={'cache_phase':'stable'}):
        failed=meter.reserve('offline')
    meter.usage(failed,state='failed')
    with ctx.research._transaction() as db:
        for seq,raw in db.execute('SELECT sequence,payload FROM model_requests').fetchall():
            item=json.loads(raw);item.update(sent_at=1,finished_at=10 if seq==failed else 2)
            db.execute('UPDATE model_requests SET payload=? WHERE sequence=?',(json.dumps(item),seq))
    result=model_usage(ctx.research)
    assert result['request_seconds']==13 and result['tps']==10
    rows={(r['scope'],r['phase']):r for r in result['cache_breakdown']}
    assert result['input_tokens']==400 and result['cache_miss_tokens']==220
    assert result['cache_hit_rate']==80/300
    assert rows['research','stable']['cache_hit_rate']==.8
    assert rows['summary','unclassified']['requests']==1
    assert rows['delegate','cold']['cache_miss_tokens'] is None
    assert rows['delegate','cold']['cache_reported_requests']==0


def test_real_agent_appends_external_edit_event_and_restores_it(tmp_path,monkeypatch):
    from ai_system.research.loop import prepare_action,resume
    import ai_system.modules.experiments.execution as execution
    seen=[];recoveries=0
    async def recover(_ctx):
        nonlocal recoveries
        recoveries+=1
        if recoveries==2:(tmp_path/'paper.md').write_text('人工新增：效率0.95')
    monkeypatch.setattr(execution,'recover_outputs',recover)
    def respond(messages):
        seen.append(messages)
        if len(seen)==1:return tool('research_list_files',{},'list')
        return tool('research_pause',{'message':'检查外部编辑后的版本，保留计算记录。'},'pause-'+str(len(seen)))
    ctx=context(tmp_path,ResearchToolsModel(respond=respond))
    assert asyncio.run(prepare_action(ctx))
    snapshots=lambda rows:[m for m in rows if m.additional_kwargs.get('context_snapshot')]
    assert len(snapshots(seen[0]))==1
    assert len(snapshots(seen[1]))==2
    assert messages_to_dict(seen[1][:len(seen[0])])==messages_to_dict(seen[0])
    assert json.loads(snapshots(seen[1])[-1].content)['document']['nonempty']
    (tmp_path/'paper.md').write_text('第二次人工修改：效率0.90')
    resume(ctx.research)
    assert asyncio.run(prepare_action(ctx))
    assert len(snapshots(seen[2]))==3
    assert json.loads(snapshots(seen[2])[-1].content)['document']['version']!=json.loads(snapshots(seen[1])[-1].content)['document']['version']
