"""Research setup and completion around the shared Agent loop."""
from uuid import uuid4
from .planner import Planner,decision_context,planning_failure_message
from .control import refresh_registry


async def run_research(ctx):
    store=ctx.research
    snapshot=store.refresh_inputs()
    from ai_system.agent.store import AgentStore
    session=AgentStore(store.root).session(store.research_id+'-lead')
    if snapshot['outcome'] in {'completed','needs_attention'} and not snapshot.get('conversation_input') and session is None:
        return {'status':'completed','messages':[]}
    ctx.skill_policy=store.skill_preferences()['policy']
    refresh_registry(ctx)
    if 'materials' not in snapshot:
        with store._transaction() as db:
            state=store._read(db);state['materials']={'inputs':store.file_inputs(),'profile':''}
            store._save(db,state,'materials_profiled',state['materials'])
    _,context=decision_context(store,store.snapshot())
    observation_id='observation:'+uuid4().hex
    try:
        result=await Planner(ctx.planner_llm).run(ctx,context,observation_id)
    except Exception as exc:
        message=planning_failure_message(exc,store.snapshot())
        with store._transaction() as db:
            state=store._read(db);state['outcome']='blocked';state['last_error']=message
            store._save(db,state,'execution_failed',{'message':message,'type':type(exc).__name__})
        await ctx.set_stage('solve','blocked',message)
        raise
    if result['status'] in {'paused','waiting'}:
        import json
        store.set_outcome('blocked')
        control=next(message for message in reversed(result['messages']) if not message.additional_kwargs.get('skipped'))
        message=json.loads(control.content)['message']
        await ctx.set_stage('solve','blocked',message)
    from .control import public_snapshot
    refresh_registry(ctx)
    await ctx.events.emit('research_state',public_snapshot(store.snapshot(),source_root=store.root))
    return result


def resume(store):
    with store._transaction() as db:
        state=store._read(db);state['action']=None;state['outcome']='running'
        store._save(db,state,'recovery_requested',{})
