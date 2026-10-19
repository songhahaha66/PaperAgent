"""State-driven action selection; LangGraph provides the durable executor."""
from langgraph.graph import END
from langgraph.types import Send

from ai_system.modules.registry import invoke
from .store import digest

DELIVERY = ('reconcile', 'write', 'validate', 'render', 'export')


def epoch(store, snapshot):
    return digest([snapshot['revision'], store.file_inputs(),
                   [(k, t['revision'], t['active_attempt'], t['validity'])
                    for k, t in snapshot['tasks'].items()]])


def flow(store):
    snapshot = store.snapshot()
    signature = epoch(store, snapshot)
    saved = snapshot.get('workflow', {})
    return saved if saved.get('epoch') == signature else {'epoch': signature, 'done': [], 'repairs': 0}


def select(state, runtime):
    """All transitions, including writing/repair/delivery, are decided here."""
    from .control import task_dispatch
    ctx = runtime.context
    if state.get('research_blocked'):
        return END
    work = task_dispatch(state, runtime)
    if work:
        return [Send('act', {'capability': 'experiments.execute', 'inputs': {'branch': item.arg}})
                for item in work]
    progress = flow(ctx.research)
    if progress.get('blocked') or state.get('artifact_conflict'):
        return END
    for step in DELIVERY:
        if step in progress['done']:
            continue
        if step == 'render' and state.get('issues'):
            if progress['repairs'] >= ctx.max_repair_rounds:
                return END
            step = 'repair'
        if step == 'export' and ctx.output_mode == 'markdown':
            return END
        return Send('act', {'capability': 'writing.' + step, 'inputs': {'state': state}})
    return END


async def act(action, runtime):
    ctx = runtime.context
    name = action['capability']
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
            if step == 'repair':
                progress['repairs'] += 1
                progress['done'] = [s for s in progress['done'] if s != 'validate']
            elif not patch.get('research_restart') and not patch.get('research_blocked'):
                if step not in progress['done']:
                    progress['done'].append(step)
            progress['blocked'] = bool(patch.get('research_blocked'))
            if 'consistency' in patch:
                snapshot['consistency'] = patch['consistency']
            snapshot['workflow'] = progress
            ctx.research._save(db, snapshot, 'capability_completed', {'capability': name, 'workflow': progress})
    return patch


def resume(store):
    """Explicit retry unblocks delivery without discarding accepted experiments."""
    with store._transaction() as db:
        snapshot = store._read(db)
        progress = snapshot.get('workflow')
        if progress and progress.get('blocked'):
            progress['blocked'] = False
            progress['repairs'] = 0
            ctx_step = snapshot.get('delivery_stages', {})
            for stage in DELIVERY:
                if ctx_step.get(stage, {}).get('status') == 'blocked':
                    progress['done'] = [s for s in progress['done'] if s != stage]
            store._save(db, snapshot, 'delivery_retry_requested', {})
