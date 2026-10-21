"""One dispatcher for experiments, artifact repairs and publication in the existing graph."""
import asyncio
import json
import time
from uuid import uuid4
from langgraph.graph import END
from langgraph.types import Send, interrupt
from ai_system.modules.registry import invoke
from .store import digest
from .domain import attach_question, report_artifact
from .planner import Planner, decision_context, planning_failure_message, ready_tasks

PUBLICATION = ('reconcile', 'write', 'validate', 'render', 'export')


def _action(store, capability, inputs, reason, *, target=None, key=None, observation_id=None, question_id=None):
    with store._transaction() as db:
        state=store._read(db)
        item={'id':key or 'action:'+digest([state['commit_seq'],capability,inputs]),
              'capability':capability,'inputs':inputs,'reason':reason,'target':target,'status':'planned',
              'action_id':key or None,'observation_id':observation_id,'experiment_id':target,
              'question_id':(state['experiments'].get(target) or {}).get('question_id',question_id),
              'prepared_at':time.time()}
        item['action_id']=item['id']
        state['action']=item
        store._save(db,state,'action_prepared',item)
    return item


def create_experiment(store, proposal):
    """Record a method under its question before it can execute."""
    from .models import ExperimentPlan
    with store._transaction() as db:
        state=store._read(db)
        key='E'+str(max((int(key[1:]) for key in state['experiments'] if key.startswith('E')),default=0)+1)
        question_id=proposal.question_id
        if question_id is None:
            question_id=next((q['id'] for q in state['questions'].values() if q['question']==proposal.question),None)
        plan=proposal.experiment or ExperimentPlan(conditions=proposal.conditions,evaluation=proposal.evaluation,
            candidates=[{'name':proposal.title or proposal.method[:100], 'instruction':proposal.method}])
        dependencies=list(dict.fromkeys(state['artifacts'][ref]['experiment_id'] for ref in proposal.artifact_refs))
        requirement_ids=list(dict.fromkeys(rid for eid in dependencies for rid in state['experiments'][eid]['requirement_ids']))
        if not requirement_ids:requirement_ids=[r['id'] for r in state['spec']['requirements']]
        instruction='；'.join(c.instruction for c in plan.candidates)
        experiment={'task_id':key,'question_id':question_id,'revision':1,
            'objective':state['questions'][question_id]['question'] if question_id else proposal.question,
            'source':state['spec']['requirement']+'\n'+'\n'.join(state['spec']['amendments']),
            'dependencies':dependencies,'requirement_ids':requirement_ids,'assumption_ids':[],
            'kind':'compute','retired':False,'execution_status':'pending','acceptance':'pending','validity':'current',
            'execution_id':None,'report_ref':None,'method_instruction':instruction,
            'experiment':plan.model_dump(),'investigates':proposal.issue_ids,'reason':proposal.reason}
        experiment['subproblem']={'id':key,'title':proposal.title or plan.candidates[0].name,
            'question':experiment['objective'],'kind':'compute','depends_on':dependencies,'deliverables':[]}
        state['experiments'][key]=experiment
        attach_question(state,experiment,question_id=question_id,statement=proposal.question,
                        title=proposal.question or proposal.title)
        from copy import deepcopy
        for requirement in state['spec']['requirements']:
            if requirement['id'] not in requirement_ids:continue
            owners={c['left']['task_id'] for c in requirement['checks'] if state['experiments'][c['left']['task_id']]['question_id']==experiment['question_id']}
            for owner in sorted(owners)[:1]:
                for check in list(requirement['checks']):
                    if check['left']['task_id']!=owner:continue
                    frozen=deepcopy(check);frozen['id']=check['id']+':'+key
                    frozen['left']['task_id']=key
                    if frozen.get('right') and frozen['right']['task_id']==owner:frozen['right']['task_id']=key
                    requirement['checks'].append(frozen)
            requirement['task_ids'].append(key)
        store._bind_assumption_inputs(state)
        state['revision']+=1
        state['max_total_attempts']=max(state['max_total_attempts'],len(state['experiments'])*state['max_attempts']+6)
        state['publication']=None
        store._save(db,state,'experiment_created',{'experiment_id':key,'question_id':experiment['question_id']})
    return key


def _save_assessments(store, proposal, *, observation_id=None, experiment_id=None, action_id=None):
    with store._transaction() as db:
        state=store._read(db)
        recorded_at=time.time()
        observation_id=observation_id or 'observation:'+uuid4().hex
        question_id=(state['experiments'].get(experiment_id) or {}).get('question_id',proposal.question_id)
        for judgment in proposal.judgments:
            question=state['questions'][judgment.question_id]
            item={**judgment.model_dump(),'recorded_at':recorded_at,
                  'observation_id':observation_id,'action_id':action_id}
            question.update({key:value for key,value in item.items() if key!='question_id'})
            question['judgment']=item['statement']
            question['judgments'].append(item)
        record={**proposal.model_dump(),'id':'decision:'+observation_id,'recorded_at':recorded_at,
                'observation_id':observation_id,'action_id':action_id,
                'question_id':question_id,'experiment_id':experiment_id}
        state.setdefault('decision_history',[]).append(record)
        state['decision_history']=state['decision_history'][-40:]
        store._save(db,state,'research_observed',record)


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
    if current and current['status']=='planned':return False
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
    # Ready experiments already have recorded method and evaluation conditions.
    ready=ready_tasks(snapshot)
    if ready:
        _action(store,'experiments.execute',{},'执行已安排的实验',target=ready[0]);return False
    from .feedback import pending_report_review
    review=pending_report_review(store)
    if review:
        _action(store,'research.review_report',review,'修订并复核实验报告',target=review['task_id']);return False
    if _publication_action(ctx,snapshot):return False
    key,context=decision_context(store,snapshot)
    if not store.reserve_planning_call(limit=ctx.max_planning_calls):
        _action(store,'pause',{},'达到配置的研究规划上限')
        return False
    observation_id='observation:'+uuid4().hex
    target=None
    try:
        from .views import activity
        from ai_system.graph.tracing import labelled
        with activity(ctx,'research.observe',observation_id=observation_id,action_id=key), labelled('observe'):
            proposal=await Planner(ctx.planner_llm).decide(snapshot,context)
        if store.snapshot()['revision']!=snapshot['revision']:
            return False
        if proposal.action=='experiment':
            if proposal.experiment_id:
                target=proposal.experiment_id
                with store._transaction() as db:
                    state=store._read(db);state['experiments'][target]['execution_status']='pending'
                    store._save(db,state,'experiment_retry_requested',{'experiment_id':target})
            else:target=create_experiment(store,proposal)
            _action(store,'experiments.execute',{},proposal.reason,target=target,key=key,observation_id=observation_id)
        elif proposal.action=='write':
            with store._transaction() as db:
                state=store._read(db)
                state['publication']={'stage':'reconcile','outline':[s.model_dump() for s in proposal.outline],
                    'title':proposal.title,'reason':proposal.reason,'repair_inputs':[],'repairs':0,
                    'observation_id':observation_id}
                store._save(db,state,'publication_requested',state['publication'])
            _publication_action(ctx,store.snapshot())
        else:
            name={'inspect':'experiments.inspect','literature':'literature.collect','tool':proposal.capability,
                  'ask':'ask','pause':'pause'}[proposal.action]
            inputs=({'query':proposal.query,'limit':3,'fetch_open_access':True} if proposal.action=='literature'
                    else {'message':proposal.message} if proposal.action=='ask' else proposal.inputs if proposal.action=='tool' else {})
            _action(store,name,inputs,proposal.reason,key=key,observation_id=observation_id,question_id=proposal.question_id)
        _save_assessments(store,proposal,observation_id=observation_id,experiment_id=target,
                          action_id=store.snapshot()['action']['id'])
        await ctx.events.emit('research_activity',{'observation_id':observation_id})
        await ctx.events.stage(proposal.reason)
        return proposal.action=='pause'
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        message=planning_failure_message(exc,store.snapshot())
        with store._transaction() as db:
            state=store._read(db)
            state['action']={'id':key,'capability':'research.observe','status':'failed','reason':message,
                             'action_id':key,'observation_id':observation_id,'finished_at':time.time(),
                             'message':message,'error':str(exc)[:3000],'error_type':type(exc).__name__}
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
        try:return await _act(request,runtime)
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
        elif name.startswith('writing.'):
            from .control import graph_view
            inputs={'state':graph_view(store.snapshot())}
        patch=await invoke(ctx,name,**inputs)
        from .models import WorkerResult
        if isinstance(patch,WorkerResult):patch={'commit':store.commit(patch)}
        if name=='experiments.inspect':
            with store._transaction() as db:
                state=store._read(db);state['materials']=patch
                store._save(db,state,'materials_profiled',patch)
        if name.startswith('writing.'):
            if ctx.output_mode != 'latex' and name in {'writing.write','writing.repair'}:
                snapshot=store.snapshot()
                sections={k:s['text'] for k,s in snapshot['sections'].items() if s['validity']=='current'}
                store.save_sections({**sections,**patch.get('sections',{})},[],patch.get('section_contexts'),verified=False)
            with store._transaction() as db:
                state=store._read(db);publication=state['publication'];step=name.split('.')[1]
                if 'consistency' in patch:state['consistency']=patch['consistency']
                if patch.get('research_restart'):
                    state['publication']=None
                elif patch.get('artifact_conflict'):
                    publication['stage']='completed'
                    state['outcome']='needs_attention'
                elif not patch.get('research_blocked'):
                    if step=='repair':
                        publication['repairs']+=1
                        publication['repair_inputs'].append(patch.pop('repair_input'))
                        publication['stage']='validate'
                    elif step=='validate' and patch.get('issues'):
                        publication['stage']='repair'
                    else:
                        next_index=PUBLICATION.index(step)+1
                        publication['stage']=PUBLICATION[next_index] if next_index<len(PUBLICATION) else 'completed'
                        if publication['stage']=='export' and ctx.output_mode=='markdown':publication['stage']='completed'
                    if publication['stage']=='completed':state['outcome']='needs_attention' if patch.get('artifact_conflict') else 'completed'
                store._save(db,state,'publication_advanced',{'step':step,'publication':state['publication']})
        with store._transaction() as db:
            state=store._read(db)
            state['action']['status']='failed' if patch.get('research_blocked') else 'done'
            if patch.get('research_blocked'):
                from .runtime import action_failure_message
                state['action']['message']=patch.get('message') or action_failure_message(action)
            state['action']['finished_at']=time.time()
            store._save(db,state,'action_finished',{'id':action['id'],'status':state['action']['status'],
                                                   'message':state['action'].get('message')})
        return patch if name=='experiments.execute' or name.startswith('writing.') else {'log':['completed '+name]}
    except asyncio.CancelledError:
        with store._transaction() as db:
            state=store._read(db);state['action'].update(status='interrupted',finished_at=time.time())
            store._save(db,state,'action_interrupted',state['action'])
        raise
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
        if action and action['status'] in {'failed','running','interrupted'}:
            # Experiment recovery is reconciled from its execution receipt. Other
            # capabilities keep their durable products and retry the failed step.
            if action['capability']=='experiments.execute':action['status']='running'
            elif action['capability'] in {'research.observe','research.review_report'}:state['action']=None
            else:action['status']='planned'
        for experiment in state['experiments'].values():
            artifact=report_artifact(state,experiment)
            if artifact and artifact.get('review'):artifact['review']['attempt_count']=0
        store._save(db,state,'recovery_requested',{'action':state['action']})
