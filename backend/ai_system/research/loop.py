"""One dispatcher for experiments, artifact repairs and publication in the existing graph."""
import asyncio
import json
import time
from uuid import uuid4
from langgraph.graph import END
from langgraph.types import Send, interrupt
from ai_system.modules.registry import invoke
from .store import digest
from .planner import Planner, NoResearchProgress, decision_context, planning_failure_message

PUBLICATION = ('validate', 'render', 'export')


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
    return _action(ctx.research,'writing.'+publication['stage'],{},publication.get('reason','整理研究成果'),
                   observation_id=publication.get('observation_id'))


async def prepare_action(ctx):
    """All actions are selected here. A routine finding is input, not a global stop."""
    store=ctx.research
    snapshot=store.snapshot()
    current=snapshot['action']
    if current and current['status']=='planned':return current['capability']=='pause'
    if current and current['status']=='failed':return True
    if snapshot['outcome'] in {'completed','needs_attention'}:return False
    if current and current['status']=='waiting':return False
    if current and current['status']=='running':
        # An experiment commit is the durable receipt, including a failed experiment.
        if current['capability']=='experiments.execute':
            experiment=snapshot['experiments'][current['target']]
            if experiment['execution_status'] not in {'running','pending'}:
                with store._transaction() as db:
                    state=store._read(db);state['action']['status']='done'
                    store._save(db,state,'action_reconciled',{'id':current['id']})
            else:return True
        else:
            return True  # An interrupted external operation needs explicit recovery.
    snapshot=store.snapshot()
    if _publication_action(ctx,snapshot):return False
    key,context=decision_context(store,snapshot)
    observation_id='observation:'+uuid4().hex
    try:
        from .views import activity
        from ai_system.graph.tracing import labelled
        with activity(ctx,'research.observe',observation_id=observation_id,action_id=key), labelled('observe'):
            await Planner(ctx.planner_llm).run(ctx,context,observation_id)
        current=store.snapshot()
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
    if runtime.context.research.snapshot()['outcome'] in {'completed','needs_attention'}:return END
    if not action or action['status']=='done':return 'observe'
    if action['capability']=='pause':return END
    return Send('act',{'action_id':action['id']})


async def act(request, runtime):
    from .runtime import execution_scope
    ctx=runtime.context
    action=ctx.research.snapshot()['action']
    if action['id']!=request['action_id'] or action['status']=='done':return {}
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
    if action['id']!=request['action_id'] or action['status']=='done':return {}
    name=action['capability']
    if name=='ask':
        with store._transaction() as db:
            state=store._read(db);state['action']['status']='waiting'
            store._save(db,state,'clarification_requested',action)
        response=interrupt({'type':'research_question','message':action['inputs']['message']})
        with store._transaction() as db:
            state=store._read(db);state.setdefault('clarifications',[]).append({
                'ref':'clarification:'+str(len(state.get('clarifications',[]))+1),'question':action['inputs']['message'],'response':str(response)[:20000]})
            state['action']['status']='done'
            store._save(db,state,'clarification_received',{'response':str(response)[:20000]})
        return {}
    with store._transaction() as db:
        state=store._read(db);state['action'].update(status='running',started_at=time.time())
        store._save(db,state,'action_started',{'id':action['id']})
    try:
        inputs=action['inputs']
        if name=='experiments.execute':
            from .control import experiment_dispatch
            inputs={'branch':experiment_dispatch(ctx,action['target'])}
        patch=await invoke(ctx,name,**inputs)
        if name.startswith('writing.') and patch.get('issues'):
            from .runtime import ResearchRuntime
            from services.chat_services.paper_project import snapshot as source_snapshot
            identity='publication:'+digest([source_snapshot(ctx.workspace)['version'],
                [i.get('message','') for i in patch['issues']]])
            meter=ResearchRuntime(ctx.research.root,ctx.research.research_id)
            meter.failed(identity,identity,'publication','正文与诊断没有变化，停止重复交付；草稿和日志已保存。')
            meter.check_guard(identity)
        if name=='experiments.inspect':
            with store._transaction() as db:
                state=store._read(db);state['materials']=patch
                store._save(db,state,'materials_profiled',patch)
        with store._transaction() as db:
            state=store._read(db)
            if name.startswith('writing.'):
                publication=state['publication'];step=name.split('.')[1]
                if 'outline' in patch:publication['outline']=patch['outline']
                if 'consistency' in patch:state['consistency']=patch['consistency']
                if patch.get('research_restart'):
                    state['publication']=None
                elif patch.get('artifact_conflict'):
                    publication['stage']='completed'
                    state['outcome']='needs_attention'
                elif not patch.get('research_blocked'):
                    if step in {'validate','export'} and patch.get('issues'):
                        # Return diagnostics to the same research conversation.
                        state['publication']=None
                    else:
                        next_index=PUBLICATION.index(step)+1
                        publication['stage']=PUBLICATION[next_index] if next_index<len(PUBLICATION) else 'completed'
                        if publication['stage']=='export' and ctx.output_mode=='markdown':publication['stage']='completed'
                    if state['publication'] and publication['stage']=='completed':state['outcome']='needs_attention' if patch.get('artifact_conflict') else 'completed'
            state['action']['status']='failed' if patch.get('research_blocked') else 'done'
            if patch.get('research_blocked'):
                from .runtime import action_failure_message
                state['action']['message']=patch.get('message') or action_failure_message(action)
            state['action']['result']=patch
            state['action']['finished_at']=time.time()
            store._save(db,state,'action_finished',{'id':action['id'],'status':state['action']['status'],
                                                   'message':state['action'].get('message')})
        return patch if name=='experiments.execute' or name.startswith('writing.') else {'log':['completed '+name]}
    except asyncio.CancelledError:
        with store._transaction() as db:
            state=store._read(db);state['action'].update(status='interrupted',finished_at=time.time())
            store._save(db,state,'action_interrupted',state['action'])
        raise
    except (ValueError,KeyError,FileNotFoundError) as exc:
        from .review_cache import error_detail
        detail=error_detail(exc)
        with store._transaction() as db:
            state=store._read(db)
            if name.startswith('writing.'):
                state['publication']=None
            state['action'].update(status='done',finished_at=time.time(),phase=name,error=detail,
                error_type=type(exc).__name__,message=detail,result={'error':detail,'phase':name})
            store._save(db,state,'action_failed',{'id':action['id'],'phase':name,'message':detail,'recoverable':True})
        return {'log':[name+': '+detail]}
    except Exception as exc:
        from .runtime import action_failure_message
        from .review_cache import error_detail
        message = action_failure_message(action)+' 原因：'+error_detail(exc)
        with store._transaction() as db:
            state=store._read(db);state['action'].update(status='failed',finished_at=time.time(),error=str(exc)[:3000],error_type=type(exc).__name__,message=message)
            store._save(db,state,'action_failed',state['action'])
        raise RuntimeError(message) from exc


def resume(store, **_):
    with store._transaction() as db:
        state=store._read(db);action=state['action']
        if action and action['capability']=='pause' and action['status']=='planned':
            # Explicit continuation consumes the pause; the next observation
            # still enforces the existing shared request budget.
            action.update(status='done',finished_at=time.time())
        elif action and action['status'] in {'failed','running','interrupted'}:
            if action['capability'].startswith('writing.') and action.get('error_type')=='RepeatedOperationBlocked':
                state['publication']=None
                action['status']='done'
                store._save(db,state,'recovery_requested',{'action':action})
                return
            # Experiment recovery is reconciled from its execution receipt. Other
            # capabilities keep their durable products and retry the failed step.
            if action['capability']=='experiments.execute':action['status']='running'
            elif action['capability'] in {'research.observe','research.revise_interpretation','research.check_interpretation'}:action['status']='done'
            else:action['status']='planned'
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='experiment_comparisons'").fetchone():
            from ai_system.modules.experiments.supervised import RECOVERABLE
            for row in db.execute('SELECT id,payload FROM experiment_comparisons WHERE research_id=?',(store.research_id,)).fetchall():
                record=json.loads(row['payload'])
                if not record.get('provider_unavailable') and any(r['status'] in RECOVERABLE for r in record['branches']):
                    record['recovery_requested']=True
                    db.execute('UPDATE experiment_comparisons SET payload=? WHERE id=?',(json.dumps(record,ensure_ascii=False),row['id']))
        store._save(db,state,'recovery_requested',{'action':state['action']})
