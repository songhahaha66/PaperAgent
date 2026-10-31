"""One dispatcher for experiments, artifact repairs and publication in the existing graph."""
import asyncio
import json
import time
from uuid import uuid4
from langgraph.graph import END
from langgraph.types import Send, interrupt
from ai_system.modules.registry import capabilities, CapabilityRequest
from .store import digest
from .plan import sync_delivery_plan
from .planner import Planner, NoResearchProgress, decision_context, planning_failure_message


def _observed_action(action):
    """A failed tool can finish its call without stopping the Agent."""
    return not action or action['status']=='done' or (action['status']=='failed' and action.get('recoverable',False))


def _set_action(state, capability, inputs, reason, *, target=None, key=None, observation_id=None, question_id=None):
    from .runtime import execution_links
    links=execution_links();key=key or links.get('action_id');observation_id=observation_id or links.get('observation_id')
    item={'id':key or 'action:'+digest([state['commit_seq'],capability,inputs]),
          'capability':capability,'inputs':inputs,'reason':reason,'target':target,'status':'planned',
          'action_id':key or None,'observation_id':observation_id,'experiment_id':target,
          'question_id':(state['experiments'].get(target) or {}).get('question_id',question_id),
          'prepared_at':time.time()}
    item['action_id']=item['id']
    state['action']=item
    return item


def _action(store, capability, inputs, reason, **links):
    with store._transaction() as db:
        state=store._read(db)
        item=_set_action(state,capability,inputs,reason,**links)
        store._save(db,state,'action_prepared',item)
    return item


def _publication_action(ctx, snapshot):
    publication=snapshot['publication']
    if not publication or publication['stage']=='completed':return None
    return _action(ctx.research,'writing.export',{},publication.get('reason','整理研究成果'),
                   observation_id=publication.get('observation_id'))


async def prepare_action(ctx):
    """All actions are selected here. A routine finding is input, not a global stop."""
    store=ctx.research
    snapshot=store.snapshot()
    current=snapshot['action']
    if current and current['status']=='planned':return current['capability']=='pause'
    if current and current['status']=='failed' and not current.get('recoverable'):return True
    if snapshot['outcome'] in {'completed','needs_attention'} and not snapshot.get('conversation_input'):return False
    if current and current['status']=='waiting':return False
    if current and current['status']=='running':
        return True  # An interrupted external operation needs explicit recovery.
    snapshot=store.snapshot()
    if _publication_action(ctx,snapshot):return False
    key,context=decision_context(store,snapshot)
    observation_id='observation:'+uuid4().hex
    try:
        from .views import activity
        from ai_system.graph.tracing import labelled
        with activity(ctx,'research.observe',observation_id=observation_id,action_id=key), labelled('observe'):
            result=await Planner(ctx.planner_llm).run(ctx,context,observation_id)
        turn=context.get('user_message')
        if turn:
            from ai_system.graph.llm_utils import message_text
            from ai_system.graph.commentary import public_progress
            with store._transaction() as db:
                state=store._read(db)
                state.pop('conversation_input',None)
                if (state.get('action') or {}).get('status')!='planned':
                    reply='\n'.join(public_progress(message_text(result['messages'][-1])))
                    if not reply:raise NoResearchProgress('本次没有可展示的回复，请继续处理。')
                    state['action']={'id':key,'capability':'reply','status':'done','result':{'reply':reply},
                                     'finished_at':time.time(),'observation_id':observation_id}
                    if turn['outcome'] in {'completed','needs_attention'}:state['outcome']=turn['outcome']
                store._save(db,state,'conversation_answered',{'id':turn['id']})
        current=store.snapshot()
        if (current.get('action') or {}).get('capability')=='reply':return False
        if (current.get('action') or {}).get('status')!='planned':
            raise NoResearchProgress('本次未获得新信息或安排下一行动，记录已保存，请继续处理或补充必要信息。')
        await ctx.events.emit('research_activity',{'observation_id':observation_id})
        action=store.snapshot().get('action') or {}
        if action.get('reason'):await ctx.events.stage(action['reason'])
        return action.get('capability')=='pause' and action.get('status')=='planned'
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A successfully scheduled operation is already durable, even if a
        # later call in the same model response failed. Keep its handoff.
        pending=store.snapshot().get('action') or {}
        if pending.get('status')=='planned':return pending['capability']=='pause'
        from ai_system.graph.llm_utils import message_text
        from .review_cache import error_detail
        candidate={'response':message_text(getattr(exc,'candidate',''))}
        message=planning_failure_message(exc,store.snapshot())
        with store._transaction() as db:
            state=store._read(db)
            state['action']={'id':key,'capability':'research.observe','status':'failed','reason':message,
                             'action_id':key,'observation_id':observation_id,'finished_at':time.time(),
                             'message':message,'error':error_detail(exc),'error_type':type(exc).__name__,
                             'phase':'decision','candidate':candidate}
            store._save(db,state,'action_failed',state['action'])
        await ctx.events.stage(message)
        return True


def select(state, runtime):
    if state.get('research_blocked'):return END
    action=runtime.context.research.snapshot()['action']
    if action and action['capability']=='reply' and action['status']=='done':return END
    if runtime.context.research.snapshot()['outcome'] in {'completed','needs_attention'}:return END
    if _observed_action(action):return 'observe'
    if action['capability']=='pause':return END
    return Send('act',{'action_id':action['id']})


async def act(request, runtime):
    from .runtime import execution_scope
    ctx=runtime.context
    action=ctx.research.snapshot()['action']
    if _observed_action(action) or action['id']!=request['action_id']:return {}
    with execution_scope(action_id=action['id'],observation_id=action.get('observation_id'),
                         experiment_id=action.get('experiment_id') or action.get('target'),
                         question_id=action.get('question_id'),events=ctx.events,
                         research_id=ctx.research.research_id,purpose=action.get('reason')):
        try:
            await _act(request,runtime)
            return {}
        finally:await ctx.events.emit('research_activity',{'action_id':action['id']})


async def _act(request, runtime):
    ctx=runtime.context;store=ctx.research
    action=store.snapshot()['action']
    if _observed_action(action) or action['id']!=request['action_id']:return {}
    name=action['capability']
    # Existing checkpoints may still point at the old deterministic stages.
    if name in {'writing.validate','writing.render'}:
        name='writing.export'
        with store._transaction() as db:
            state=store._read(db);state['action']['capability']=name
            if state.get('publication'):state['publication']['stage']='export'
            store._save(db,state,'delivery_resumed',{'id':action['id']})
    if name=='ask':
        with store._transaction() as db:
            state=store._read(db);state['action']['status']='waiting'
            store._save(db,state,'clarification_requested',action)
        response=interrupt({'type':'research_question','message':action['inputs']['message']})
        from .clarifications import receive
        receive(store,action['id'],str(response))
        return {}
    with store._transaction() as db:
        state=store._read(db);state['action'].update(status='running',started_at=time.time())
        if name == 'writing.export':sync_delivery_plan(state, 'in_progress')
        store._save(db,state,'action_started',{'id':action['id']})
    from .runtime import ResearchRuntime
    meter=ResearchRuntime(store.root,store.research_id)
    operation='action:'+digest([name,action['inputs'],action.get('target'),store.file_inputs()])
    try:
        if name.startswith('writing.'):
            from services.chat_services.paper_project import snapshot as source_snapshot
            operation=digest([operation,source_snapshot(ctx.workspace)['version']])
        meter.check_guard(operation)
        inputs=action['inputs']
        product=await capabilities().invoke(CapabilityRequest(name=name,inputs=inputs),ctx)
        patch=product.value
        if name.startswith('writing.') and patch.get('issues'):
            from services.chat_services.paper_project import snapshot as source_snapshot
            identity='publication:'+digest([source_snapshot(ctx.workspace)['version'],
                [i.get('message','') for i in patch['issues']]])
            meter.failed(identity,identity,'publication','正文与诊断没有变化，停止重复交付；草稿和日志已保存。')
            meter.check_guard(identity)
        if name=='experiments.inspect':
            with store._transaction() as db:
                state=store._read(db);state['materials']=patch
                store._save(db,state,'materials_profiled',patch)
        with store._transaction() as db:
            state=store._read(db)
            if name.startswith('writing.'):
                publication=state['publication']
                if 'outline' in patch:publication['outline']=patch['outline']
                if 'consistency' in patch:state['consistency']=patch['consistency']
                if patch.get('research_restart'):
                    state['publication']=None
                elif patch.get('artifact_conflict'):
                    publication['stage']='completed'
                    state['outcome']='needs_attention'
                elif not patch.get('research_blocked'):
                    if patch.get('issues'):
                        # Return diagnostics to the same research conversation.
                        state['publication']=None
                    else:
                        publication['stage']='completed'
                    if state['publication'] and publication['stage']=='completed':state['outcome']='needs_attention' if patch.get('artifact_conflict') else 'completed'
            failed=product.execution_status in {'failed','interrupted'}
            state['action']['status']='failed' if patch.get('research_blocked') or failed else 'done'
            state['action']['recoverable']=failed and not patch.get('research_blocked')
            if failed:state['action']['message']=product.execution_message or '本次执行未完成，请根据已有记录决定下一步。'
            if patch.get('research_blocked'):
                from .runtime import action_failure_message
                state['action']['message']=patch.get('message') or action_failure_message(action)
            state['action']['result']=patch
            state['action']['finished_at']=time.time()
            if name == 'writing.export':
                if (state.get('publication') or {}).get('stage') == 'completed' and state['outcome'] == 'completed' and state['action']['status'] == 'done':
                    sync_delivery_plan(state, 'completed')
                else:
                    sync_delivery_plan(state, 'blocked', state['action'].get('message') or '导出尚未完成，需处理交付诊断。')
            store._save(db,state,'action_finished',{'id':action['id'],'status':state['action']['status'],
                                                   'message':state['action'].get('message')})
        if failed:meter.failed(operation,digest(product.execution_message),'execution',state['action']['message'])
        else:meter.succeeded(operation)
        return patch if name.startswith('writing.') else {'log':['completed '+name]}
    except asyncio.CancelledError:
        with store._transaction() as db:
            state=store._read(db);state['action'].update(status='interrupted',finished_at=time.time())
            if name == 'writing.export':sync_delivery_plan(state, 'blocked', '导出已中断，等待继续。')
            store._save(db,state,'action_interrupted',state['action'])
        raise
    except (ValueError,KeyError,FileNotFoundError) as exc:
        from .review_cache import error_detail
        detail=error_detail(exc)
        meter.failed(operation,digest(detail),'tool_input',detail)
        with store._transaction() as db:
            state=store._read(db)
            if name.startswith('writing.'):
                state['publication']=None
            state['action'].update(status='failed',recoverable=True,finished_at=time.time(),phase=name,error=detail,
                error_type=type(exc).__name__,message=detail,result={'error':detail,'phase':name})
            if name == 'writing.export':sync_delivery_plan(state, 'blocked', detail)
            store._save(db,state,'action_failed',{'id':action['id'],'phase':name,'message':detail,'recoverable':True})
        return {'log':[name+': '+detail]}
    except Exception as exc:
        from .runtime import action_failure_message
        from .review_cache import error_detail
        message = action_failure_message(action)+' 原因：'+error_detail(exc)
        with store._transaction() as db:
            state=store._read(db);state['action'].update(status='failed',recoverable=False,finished_at=time.time(),error=str(exc)[:3000],error_type=type(exc).__name__,message=message)
            if name == 'writing.export':sync_delivery_plan(state, 'blocked', message)
            store._save(db,state,'action_failed',state['action'])
        raise RuntimeError(message) from exc


def resume(store, **_):
    with store._transaction() as db:
        state=store._read(db);action=state['action']
        if action and action['capability']=='pause' and action['status']=='planned':
            # Explicit continuation consumes the pause; the next observation
            # still enforces the existing shared request budget.
            action.update(status='done',finished_at=time.time())
        elif action and not _observed_action(action) and action['status'] in {'failed','running','interrupted'}:
            if action['capability'].startswith('writing.') and action.get('error_type')=='RepeatedOperationBlocked':
                state['publication']=None
                action['status']='done'
                store._save(db,state,'recovery_requested',{'action':action})
                return
            # Durable products are retained when resuming an interrupted step.
            if action['capability'] in {'research.observe','research.revise_interpretation','research.check_interpretation'}:action['status']='done'
            else:action['status']='planned'
        store._save(db,state,'recovery_requested',{'action':state['action']})
