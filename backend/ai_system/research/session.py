"""Persistent Agent sessions on the existing graph database.

The outer graph schedules capabilities. A stable session thread retains messages
across those transitions; neither Python memory nor business state is replayed.
"""
from contextlib import asynccontextmanager
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from services.file_services.workspace_layout import workspace_path


@asynccontextmanager
async def session_saver(ctx):
    path=workspace_path(ctx.research.root,'.system/graph.sqlite')
    path.parent.mkdir(parents=True,exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        yield saver


async def run_session(agent, session, messages, *, on_resume=None):
    # Do not inherit the outer node's invocation-scoped checkpoint namespace.
    parent=var_child_runnable_config.get() or {}
    token=var_child_runnable_config.set(None)
    config={'configurable':{'thread_id':session},'recursion_limit':100000}
    if parent.get('callbacks'):config['callbacks']=parent['callbacks']
    try:
        saved=await agent.aget_state(config)
        if saved.values:
            if on_resume:on_resume(bool(saved.next))
            seen={m.id for m in saved.values.get('messages',[]) if m.id}
            incoming=[m for m in messages[-1:] if not m.id or m.id not in seen]
            value=None if saved.next else {'messages':incoming}
        else:value={'messages':messages}
        return await agent.ainvoke(value,config=config,durability='sync')
    finally:var_child_runnable_config.reset(token)


async def invoke_once(ctx, name, inputs, key):
    """Reuse a completed tool response; reconcile submitted code before any replay."""
    import json
    from .store import digest
    from ai_system.modules.registry import invoke
    store=ctx.research;receipt='tool-response:'+key;signature=digest([name,inputs])
    with store._connect() as db:
        row=db.execute('SELECT digest,response FROM commands WHERE research_id=? AND id=?',(store.research_id,receipt)).fetchone()
    if row:
        if row[0]!=signature:raise ValueError('同一工具调用不能更改参数，请发起新调用')
        return json.loads(row[1])
    if name == 'research.run_python':
        from .runs import direct_runs, run_result
        submitted = next((rid for rid, info in direct_runs(store, ctx.fs).items() if info.get('tool_call_id') == key), None)
        if submitted:
            result = {**run_result(ctx, submitted), 'recovered': True,
                      'note': '复用已有运行回执；未重放代码。执行未知时先核实日志。'}
            with store._transaction() as db:
                db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',
                           (store.research_id, receipt, signature, json.dumps(result, ensure_ascii=False)))
            return result
    attempt=next((a for a in store.snapshot()['executions'].values() if a.get('tool_call_id')==key),None) if name=='research.run_python' else None
    if attempt:
        runs=[ctx.fs.get_run_info(rid) or {} for rid in attempt.get('run_ids',[])]
        text='\n'.join(ctx.fs.read_text(r['stdout']) for r in runs if r.get('stdout') and ctx.fs.exists(r['stdout']))
        result={'experiment_id':attempt['task_id'],'execution_status':attempt['execution_status'],
            'run_ids':attempt.get('run_ids',[]),'observation':text[-6000:],
            'recovered':True,'note':'复用了已提交运行；未重放代码。执行未知时读取日志核实。'}
    else:result=await invoke(ctx,name,**inputs)
    with store._transaction() as db:
        db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',(store.research_id,receipt,signature,json.dumps(result,ensure_ascii=False,default=str)))
    return result
