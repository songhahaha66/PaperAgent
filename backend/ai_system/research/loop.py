"""State-driven action selection; LangGraph provides the durable executor."""
from langgraph.graph import END
from langgraph.types import Send

from ai_system.modules.registry import invoke
from . import DEFAULT_PLANNING_CALL_LIMIT
import json

DELIVERY = ('reconcile', 'write', 'validate', 'render', 'export')


def epoch(store, snapshot):
    from .planner import decision_context
    return decision_context(store,snapshot)[0]


def flow(store):
    snapshot = store.snapshot()
    signature = epoch(store, snapshot)
    saved = snapshot.get('workflow', {})
    return saved if saved.get('epoch') == signature else {'epoch': signature, 'done': [], 'repairs': 0}


def writing_version(snapshot):
    from .store import digest
    return digest({'revision':snapshot['revision'],
        'sections':{k:[s['content_hash'],s['validity']] for k,s in snapshot['sections'].items()},
        'findings':[(i['id'],i['category'],i['action']) for i in snapshot['feedback'] if i['status'] not in {'resolved','dismissed','superseded'}],
        'tasks':{k:[t['revision'],t['active_attempt'],t['acceptance'],t['validity']] for k,t in snapshot['tasks'].items()},
        'evidence':{k:e['payload'] for k,e in snapshot['evidence'].items() if e['active']}})


def writing_action(store):
    """Repair existing chapter feedback before asking for a new research plan."""
    from types import SimpleNamespace
    from .domain import findings
    from ai_system.modules.writing.consistency import section_doubts
    snapshot=store.snapshot()
    if not snapshot['sections'] or any(not t['retired'] and (t['acceptance']!='accepted' or t['validity']!='current') for t in snapshot['tasks'].values()):return None
    active=[i for i in findings(snapshot) if i['blocking'] and i['status'] not in {'resolved','dismissed','superseded'}]
    doubts=section_doubts({'sections':snapshot['sections']},SimpleNamespace(research=store))
    editorial=[i for i in active if i['category']=='writing' and i['section'] and i['section']!='paper'
               and (i['section'] in snapshot['sections'] or i['source'] in {'sections','review-task:sections'})]
    review_faults=[i for i in active if i['category']=='service' and i.get('review_scope',{}).get('kind')!='candidate'
                   and (i.get('review_scope',{}).get('stage')=='sections' or i['source']=='sections')]
    if not editorial and not doubts and not review_faults:return None
    if any(i not in editorial and i not in review_faults and i['id'] not in doubts for i in active):return None
    if review_faults:return 'validate'
    if snapshot.get('workflow',{}).get('validation_version')!=writing_version(snapshot):return 'validate'
    # A reviewed scientific doubt goes back to research, never to a text repair.
    return 'repair' if editorial and not doubts else None


def select(state, runtime):
    """All transitions, including writing/repair/delivery, are decided here."""
    from .control import task_dispatch
    ctx = runtime.context
    if state.get('research_blocked'):
        return END
    editorial=writing_action(ctx.research)
    if editorial:return Send('act',{'capability':'writing.'+editorial,'inputs':{'state':state}})
    from .planner import literature_action,current_decision
    selected=current_decision(ctx.research)
    if not selected:return 'observe'
    key,record=selected;proposal=record['proposal']
    if proposal['action']=='execute':
        if proposal.get('changes') or proposal.get('requirements'):
            from .models import PlanPatch
            # One controller call can create the first task and execute it.
            # The patch receipt makes a repeated dispatch idempotent.
            ctx.research.apply_patch(PlanPatch(command_id=key+':apply',base_revision=record['base_revision'],
                reason=proposal['reason'],changes=proposal.get('changes',[]),requirements=proposal.get('requirements',[])))
        work=task_dispatch(state,runtime,proposal['task_ids'])
        return [Send('act', {'capability': 'experiments.execute', 'inputs': {'branch': item.arg},
                            'decision_key':key,'task_id':item.arg['subproblem']['id']})
                for item in work] if work else 'observe'
    if proposal['action'] in {'inspect','ask','capability'}:
        return Send('act',{'capability':proposal['capability'] if proposal['action']=='capability' else 'experiments.inspect' if proposal['action']=='inspect' else 'ask',
                           'inputs':proposal.get('inputs',{}),'decision_key':key})
    literature=literature_action(ctx.research)
    if literature:
        return Send('act',literature)
    if proposal['action']!='deliver':return 'observe'
    progress = flow(ctx.research)
    if progress.get('blocked') or state.get('artifact_conflict'):
        return END
    for step in DELIVERY:
        if step in progress['done']:
            continue
        if step == 'render' and state.get('issues'):
            step = 'repair'
        if step == 'export' and ctx.output_mode == 'markdown':
            return END
        return Send('act', {'capability': 'writing.' + step, 'inputs': {'state': state}})
    return END


async def act(action, runtime):
    ctx = runtime.context
    name = action['capability']
    if action.get('decision_key') and name=='literature.collect':
        from .planner import begin_collection,finish_collection
        key=action['decision_key']
        begin_collection(ctx.research,key)
        try:
            result=await invoke(ctx,name,**action['inputs'])
        except Exception:
            # A saved operation may have completed before the service failed.
            # observe reconciles it; no blind network replay on resume.
            await ctx.events.stage('文献查证未完成，已保留执行位置；将核对已保存的检索记录，暂不交付。')
            return {'log':['literature collection interrupted']}
        await finish_collection(ctx,key,result)
        return {'log':['literature evidence saved for the next research decision']}
    if action.get('decision_key'):
        key=action['decision_key'];store=ctx.research;record=store.planning_decision(key)
        if name=='ask':
            if record.get('status')=='replied':return {}
            from langgraph.types import interrupt
            reply=interrupt({'type':'research_question','message':record['proposal']['question']})
            store.save_planning_decision(key,{**record,'status':'replied','response':str(reply)[:20000]})
            return {'log':['research clarification received']}
        if record['proposal']['action']=='capability':
            from ai_system.modules.registry import capabilities
            capabilities().validate_research_tool(name,action['inputs'])
        receipt_key=key+(':execute:'+action['task_id'] if name=='experiments.execute' else ':inspect')
        receipt=store.planning_decision(receipt_key)
        if receipt and receipt['status']=='done':return {}
        if receipt:
            # An uncertain operation is observed, never blindly replayed.
            store.save_planning_decision(key,{**record,'status':'operation_uncertain'})
            return {'log':['research action uncertain; observe without replay']}
        store.save_planning_decision(receipt_key,{'status':'executing','capability':name})
        patch=await invoke(ctx,name,**action['inputs'])
        from .models import WorkerResult
        if isinstance(patch,WorkerResult):
            # Extension capabilities can return the existing frozen worker
            # contract; only ResearchStore publishes accepted artifacts.
            patch={'commit':store.commit(patch)}
        if name=='experiments.inspect':
            with store._transaction() as db:
                snapshot=store._read(db);snapshot['materials']=patch
                store._save(db,snapshot,'materials_profiled',patch)
        store.save_planning_decision(receipt_key,{'status':'done','capability':name})
        if name!='experiments.execute' or all((store.planning_decision(key+':execute:'+task) or {}).get('status')=='done'
                                            for task in record['proposal']['task_ids']):
            store.save_planning_decision(key,{**record,'status':'done','result':json.loads(json.dumps(patch,ensure_ascii=False)) if len(json.dumps(patch,ensure_ascii=False))<=10000 else {'note':'产物已保存，观察摘要超过长度上限；不作为科学结果。'}})
        return patch if name=='experiments.execute' else {'data_profile':patch['profile'],'log':['materials inspected']} if name=='experiments.inspect' else {'log':['registered research capability completed: '+name]}
    # A failed call never advances workflow progress. Recovery selects it again.
    patch = await invoke(ctx, name, **action['inputs'])
    if name.startswith('writing.'):
        step = name.split('.', 1)[1]
        if step in {'write', 'repair'}:
            before = action['inputs']['state']
            ctx.research.save_sections({**before.get('sections', {}), **patch.get('sections', {})},
                before.get('issues', []), patch.get('section_contexts', before.get('section_contexts')), verified=False)
        with ctx.research._transaction() as db:
            snapshot = ctx.research._read(db)
            signature = epoch(ctx.research, snapshot)
            progress = snapshot.get('workflow', {})
            if progress.get('epoch') != signature:
                progress = {'epoch': signature, 'done': [], 'repairs': 0}
            if patch.get('research_restart'):
                progress['done']=[]
            elif step == 'repair' and not patch.get('research_blocked'):
                progress['repairs'] += 1
                progress['repair_inputs'] = [*progress.get('repair_inputs', []), patch.pop('repair_input')][-12:]
                progress['done'] = [s for s in progress['done'] if s != 'validate']
            elif not patch.get('research_restart') and not patch.get('research_blocked'):
                if step not in progress['done']:
                    progress['done'].append(step)
            progress['blocked'] = bool(patch.get('research_blocked'))
            if step=='validate':progress['validation_version']=writing_version(snapshot)
            if not progress['blocked'] and (step == 'export' or step == 'render' and ctx.output_mode == 'markdown'):
                if ctx.research.delivery_ready(snapshot):
                    snapshot['outcome'] = 'needs_attention' if patch.get('artifact_conflict') else 'completed'
            if 'consistency' in patch:
                snapshot['consistency'] = patch['consistency']
            snapshot['workflow'] = progress
            ctx.research._save(db, snapshot, 'capability_completed', {'capability': name, 'workflow': progress})
    return patch


def resume(store, *, planning_limit=DEFAULT_PLANNING_CALL_LIMIT):
    """Explicit retry unblocks delivery without discarding accepted experiments."""
    with store._transaction() as db:
        snapshot = store._read(db)
        retried=[]
        for row in db.execute("SELECT id,response FROM commands WHERE research_id=? AND (id LIKE 'research-next:%' OR id LIKE 'planner-repair:%')",
                              (store.research_id,)).fetchall():
            record=json.loads(row[1])
            if record.get('status') in {'planning','planning_failed'} or (record.get('status')=='done' and record.get('proposal',{}).get('action')=='pause') or (
                    record.get('status')=='limited' and (planning_limit is None or snapshot['planning_calls']<planning_limit)):
                record['status']='retry'
                db.execute('UPDATE commands SET response=? WHERE research_id=? AND id=?',
                           (json.dumps(record,ensure_ascii=False),store.research_id,row[0]))
                retried.append(row[0])
        if retried:
            store._save(db,snapshot,'research_decision_retry_requested',{'decisions':retried})
        progress = snapshot.get('workflow')
        if progress and progress.get('blocked'):
            progress['blocked'] = False
            progress['repairs'] = 0
            progress['repair_inputs'] = []
            ctx_step = snapshot.get('delivery_stages', {})
            for stage in DELIVERY:
                if ctx_step.get(stage, {}).get('status') == 'blocked':
                    progress['done'] = [s for s in progress['done'] if s != stage]
            store._save(db, snapshot, 'delivery_retry_requested', {})
