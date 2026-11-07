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


async def run_session(agent, session, messages, *, ctx=None, on_resume=None, new_user_turn_id=None):
    if ctx is not None:
        from ai_system.agent.context import CURRENT_TOOL, CURRENT_TURN, session_turn
        from ai_system.agent.protocol import MessageItem
        from uuid import uuid4
        import time
        parent = CURRENT_TURN.get()
        if CURRENT_TOOL.get() is not None or parent is None:
            child = parent is not None
            with session_turn(ctx, session) as turn:
                if child and messages:
                    entry=MessageItem(id=uuid4().hex,thread_id=turn.thread_id,turn_id=turn.turn_id,
                                      type='userMessage',text=str(messages[-1].content),started_at=time.time())
                    turn.put(entry.model_dump(), 'item/started')
                    entry.status='completed';entry.finished_at=time.time()
                    turn.put(entry.model_dump(), 'item/completed')
                result=await run_session(agent,session,messages,on_resume=on_resume,new_user_turn_id=new_user_turn_id)
                if child:
                    from ai_system.graph.commentary import public_progress
                    text='\n\n'.join(public_progress(result['messages'][-1].content))
                    entry=MessageItem(id=uuid4().hex,thread_id=turn.thread_id,turn_id=turn.turn_id,
                                      type='agentMessage',phase='final_answer',text=text,started_at=time.time())
                    turn.put(entry.model_dump(), 'item/started')
                    entry.status='completed';entry.finished_at=time.time()
                    turn.put(entry.model_dump(), 'item/completed')
                return result
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
            if saved.next and incoming and new_user_turn_id and new_user_turn_id not in seen:
                # A new request takes precedence over tools left scheduled by
                # the previous turn. Pair old calls without replaying them;
                # ToolRuntime still reconciles any already-submitted products.
                from langchain_core.messages import ToolMessage
                history=saved.values.get('messages',[])
                answered={m.tool_call_id for m in history if isinstance(m,ToolMessage)}
                skipped=[ToolMessage(tool_call_id=call['id'],name=call['name'],status='error',
                    content='新用户消息已到达，此处没有重放旧工具调用。先核实已有提交的运行回执，再按最新要求决定下一步。')
                    for m in history for call in getattr(m,'tool_calls',[]) if call['id'] not in answered]
                if skipped:
                    await agent.aupdate_state(config,{'messages':[*skipped,*incoming]},as_node='tools')
                    value=None
                else:
                    value={'messages':incoming}
            else:
                value=None if saved.next else {'messages':incoming}
        else:value={'messages':messages}
        return await agent.ainvoke(value,config=config,durability='sync')
    finally:var_child_runnable_config.reset(token)
