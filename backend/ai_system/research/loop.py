"""One dispatcher for experiments, artifact repairs and publication in the existing graph."""
import asyncio
import json
import time
from uuid import uuid4
from langgraph.graph import END
from langgraph.types import Send, interrupt
from ai_system.modules.registry import invoke
from .store import digest
from .models import ResearchConflict
from .planner import Planner, decision_context, planning_failure_message

PUBLICATION = ('write', 'validate', 'render', 'export')


def _set_action(state, capability, inputs, reason, *, target=None, key=None, observation_id=None, question_id=None):
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


def _create_experiment(store, state, proposal, question_id):
    from .models import ExperimentPlan
    key='E'+str(max((int(key[1:]) for key in state['experiments'] if key.startswith('E') and key[1:].isdigit()),default=0)+1)
    plan=proposal.experiment or ExperimentPlan(conditions=proposal.conditions,evaluation=proposal.evaluation,
        candidates=[{'name':proposal.title or proposal.method[:100], 'instruction':proposal.method}])
    dependencies=list(dict.fromkeys(state['artifacts'][ref]['experiment_id'] for ref in proposal.artifact_refs))
    owners=set(dependencies) | {eid for eid,e in state['experiments'].items() if e['question_id']==question_id}
    requirement_ids=[r['id'] for r in state['spec']['requirements'] if owners.intersection(r['task_ids'])]
    if not requirement_ids:requirement_ids=[r['id'] for r in state['spec']['requirements']]
    instruction='；'.join(c.instruction for c in plan.candidates)
    experiment={'task_id':key,'question_id':question_id,'revision':1,
        'objective':state['questions'][question_id]['question'],
        'source':state['spec']['requirement']+'\n'+'\n'.join(state['spec']['amendments']),
        'dependencies':dependencies,'requirement_ids':requirement_ids,'assumption_ids':[],
        'kind':'compute','retired':False,'execution_status':'pending','acceptance':'pending','validity':'current',
        'execution_id':None,'report_ref':None,'method_instruction':instruction,
        'experiment':plan.model_dump(),'investigates':proposal.issue_ids,'reason':proposal.reason}
    experiment['subproblem']={'id':key,'title':proposal.title or plan.candidates[0].name,
        'question':experiment['objective'],'kind':'compute','depends_on':dependencies,'deliverables':[]}
    state['experiments'][key]=experiment
    for requirement in state['spec']['requirements']:
        if requirement['id'] in requirement_ids:requirement['task_ids'].append(key)
    store._bind_assumption_inputs(state)
    state['max_total_attempts']=max(state['max_total_attempts'],len(state['experiments'])*state['max_attempts']+6)
    state['publication']=None
    return key


def create_experiment(store, proposal):
    from .questions import apply_questions
    from .planner import resolve_question
    with store._transaction() as db:
        state=store._read(db)
        updated=apply_questions(state,proposal.question_updates)
        qid=resolve_question(proposal,updated)
        key=_create_experiment(store,state,proposal,qid)
        state['revision']+=1
        store._save(db,state,'experiment_created',{'experiment_id':key,'question_id':qid})
    return key


def _assess(state, proposal, *, observation_id=None, experiment_id=None, action_id=None, question_id=None):
    recorded_at=time.time()
    observation_id=observation_id or 'observation:'+uuid4().hex
    question_id=(state['experiments'].get(experiment_id) or {}).get('question_id',question_id or proposal.question_id)
    for judgment in proposal.judgments:
        question=state['questions'][judgment.question_id]
        item={**judgment.model_dump(),'recorded_at':recorded_at,
              'observation_id':observation_id,'action_id':action_id}
        question.update({key:item[key] for key in ('artifact_refs','limitations','next_step','coverage','stop_reason')})
        question.pop('statement',None)
        question['judgment']=item['statement']
        question['answer_status']='current'
        question['basis_versions']={ref:state['artifacts'][ref]['content_hash'] for ref in item['artifact_refs']}
        question['interpretation_versions']={eid:state['experiments'][eid]['report_ref'] for eid in {state['artifacts'][ref]['experiment_id'] for ref in item['artifact_refs']}}
        question['judgments'].append(item)
    record={**proposal.model_dump(),'id':'decision:'+observation_id,'recorded_at':recorded_at,
            'observation_id':observation_id,'action_id':action_id,
            'question_id':question_id,'experiment_id':experiment_id}
    state.setdefault('decision_history',[]).append(record)
    state['decision_history']=state['decision_history'][-40:]
    return record


def _save_assessments(store, proposal, **links):
    with store._transaction() as db:
        state=store._read(db)
        record=_assess(state,proposal,**links)
        store._save(db,state,'research_observed',record)


def commit_decision(store, proposal, context, *, base_revision, key, observation_id):
    """Publish one decision and its linked changes together, before running tools."""
    from .questions import apply_questions
    from .planner import resolve_question, validate_action
    from .models import ResearchConflict
    command='decision:'+key
    signature=digest(proposal.model_dump())
    with store._transaction() as db:
        previous=db.execute('SELECT digest,response FROM commands WHERE research_id=? AND id=?',
                            (store.research_id,command)).fetchone()
        if previous:
            if previous[0]!=signature:raise ResearchConflict('该决策已按另一版本提交')
            return json.loads(previous[1])
        state=store._read(db)
        if state['revision']!=base_revision:raise ResearchConflict('研究版本已更新，重新读取当前记录')
        validate_action(proposal,state,context)
        updated=apply_questions(state,proposal.question_updates)
        qid=resolve_question(proposal,updated);target=None
        if proposal.action in {'experiment','continue'}:
            target=proposal.experiment_id
            if target:
                experiment=state['experiments'][target]
                if proposal.action=='continue':
                    experiment['continuation']={'parent_attempt':experiment['execution_id'],'information_need':proposal.information_need}
                experiment['execution_status']='pending'
            else:target=_create_experiment(store,state,proposal,qid)
            name='experiments.execute';inputs={}
        elif proposal.action=='write':
            state['publication']={'stage':'write','outline':[v.model_dump() for v in proposal.outline],
                'title':proposal.title,'reason':proposal.reason,'repair_inputs':[],'repairs':0,'observation_id':observation_id}
            name='writing.write';inputs={}
        else:
            name={'inspect':'experiments.inspect','literature':'literature.collect','tool':proposal.capability,
                  'ask':'ask','pause':'pause','observe':'research.update'}[proposal.action]
            inputs=({'query':proposal.query,'limit':3,'fetch_open_access':True} if proposal.action=='literature'
                else {'message':proposal.message} if proposal.action=='ask' else proposal.inputs if proposal.action=='tool' else {})
            target=inputs.get('task_id') or proposal.experiment_id
        action=_set_action(state,name,inputs,proposal.reason,target=target,key=key,observation_id=observation_id,question_id=qid)
        if proposal.action=='observe':action['status']='done'
        record=_assess(state,proposal,observation_id=observation_id,experiment_id=target,action_id=action['id'],question_id=qid)
        state['revision']+=1
        receipt={'action_id':action['id'],'experiment_id':target,'question_ids':updated,'revision':state['revision']}
        db.execute('INSERT INTO commands VALUES (?,?,?,?)',(store.research_id,command,signature,json.dumps(receipt)))
        store._save(db,state,'research_observed',record)
    return receipt


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
    if _publication_action(ctx,snapshot):return False
    key,context=decision_context(store,snapshot)
    if not store.reserve_planning_call(limit=ctx.max_planning_calls):
        _action(store,'pause',{},'达到配置的研究规划上限')
        return False
    observation_id='observation:'+uuid4().hex
    proposal=None
    try:
        from .views import activity
        from ai_system.graph.tracing import labelled
        with activity(ctx,'research.observe',observation_id=observation_id,action_id=key), labelled('observe'):
            proposal=await Planner(ctx.planner_llm).decide(snapshot,context)
        commit_decision(store,proposal,context,base_revision=snapshot['revision'],key=key,observation_id=observation_id)
        await ctx.events.emit('research_activity',{'observation_id':observation_id})
        await ctx.events.stage(proposal.reason)
        return proposal.action=='pause'
    except asyncio.CancelledError:
        raise
    except ResearchConflict:
        return False
    except Exception as exc:
        from ai_system.graph.llm_utils import message_text
        from .review_cache import error_detail
        candidate=proposal.model_dump() if proposal else {'response':message_text(getattr(exc,'candidate',''))}
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
            state=store._read(db)
            if name.startswith('writing.'):
                publication=state['publication'];step=name.split('.')[1]
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
                    elif step in {'validate','export'} and patch.get('issues'):
                        publication['stage']='repair'
                    else:
                        next_index=PUBLICATION.index(step)+1
                        publication['stage']=PUBLICATION[next_index] if next_index<len(PUBLICATION) else 'completed'
                        if publication['stage']=='export' and ctx.output_mode=='markdown':publication['stage']='completed'
                    if publication['stage']=='completed':state['outcome']='needs_attention' if patch.get('artifact_conflict') else 'completed'
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
        if action and action['status'] in {'failed','running','interrupted'}:
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
