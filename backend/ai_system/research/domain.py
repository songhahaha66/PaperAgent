"""Question → experiment → artifact. Only ResearchStore owns research state."""
from services.file_services.workspace_layout import workspace_path
from itertools import chain

SCHEMA_VERSION = 5
FAULT_CATEGORIES = frozenset({"execution", "service"})


def findings(state):
    return list(chain(state["feedback"], state["faults"]))


def experiment_scope(experiments, experiment_ids):
    """An explicit experiment scope plus its existing recursive dependencies."""
    scope = set(experiment_ids)
    pending = list(scope)
    while pending:
        experiment = experiments.get(pending.pop())
        if experiment is None:
            continue
        for dependency in experiment.get('dependencies', []):
            if dependency in experiments and dependency not in scope:
                scope.add(dependency)
                pending.append(dependency)
    return scope


def execution_lineage(state, execution_id):
    """Current execution first, then its saved ancestors, without replaying code."""
    ids=[]
    while execution_id and execution_id not in ids:
        ids.append(execution_id)
        execution_id=state['executions'].get(execution_id,{}).get('parent_attempt')
    return ids



def result_semantic_key(meaning,unit=''):
    """Declared statistical identity; lineage remains provenance, not scope."""
    if not meaning or not all(meaning.get(k) for k in ('object','scope','statistic')):return None
    return tuple(meaning.get(k,'') for k in ('object','scope','statistic','conditions'))+(unit,)


def current_results(state, experiment_ids=None, *, task_id=None, results=()):
    """One effective execution per named result; explicit refs retain provenance.

    Candidates override by ID, so inherited outputs are not counted twice.
    A newer execution replaces a same-name, same declared-scope result.
    Different declared objects/scopes/conditions or units keep their own refs.
    Multiple IDs within that same execution remain ambiguous for name selectors;
    evaluate reports an inconclusive check instead of guessing a value.
    """
    ranks={}
    for eid, experiment in state['experiments'].items():
        if experiment_ids is not None and eid not in experiment_ids:continue
        if experiment['retired'] or experiment['validity']!='current':continue
        if eid!=task_id and (experiment['kind']=='verify' or experiment['acceptance']!='accepted'):continue
        ranks.update({aid:index for index,aid in enumerate(execution_lineage(state,experiment['execution_id']))})
    entries={ref:entry for ref,entry in state['artifacts'].items()
             if entry['type']=='result' and entry['active'] and entry['producer_attempt'] in ranks}
    for item in results:
        previous=state['artifacts'].get(item['id'])
        if previous and (not previous['active'] or previous['producer_attempt'] not in ranks):continue
        entries[item['id']]=previous or {'payload':item,'producer_attempt':state['experiments'][task_id]['execution_id']}
    selected={}
    for entry in entries.values():
        item=entry['payload'];key=(item['subproblem'],item['name'],result_semantic_key(item.get('meaning'),item.get('unit','')));rank=ranks.get(entry['producer_attempt'],0)
        if key not in selected or rank < selected[key][0]:selected[key]=(rank,[])
        if rank==selected[key][0]:selected[key][1].append(item)
    return [item for _,items in selected.values() for item in items]


def current_result_refs(state, experiment_ids):
    """Available evidence is distinct from whether a question is answered."""
    return {item['id'] for item in current_results(state,experiment_ids)}


def attach_question(state, experiment, *, question_id=None, statement=None, title=None):
    """Attach once, including imported experiments. A method never owns a question."""
    questions = state['questions']
    if question_id is None:
        question_id = 'question:' + experiment['task_id']
        questions[question_id] = {'id': question_id, 'title': title or statement or experiment['objective'],
            'question': statement or experiment['objective'], 'judgment': '', 'artifact_refs': [],
            'limitations': [], 'next_step': '', 'judgments': [], 'version':1, 'answer_status':'unanswered'}
    experiment['question_id'] = question_id
    return questions[question_id]


def register_execution_artifacts(state, root, execution):
    """Index actual retained files; source ownership comes from the execution workspace."""
    import hashlib
    import json
    aid=execution['attempt_id'];eid=execution['task_id']
    workspace=root/'experiments'/aid/'workspace'
    manifest=workspace_path(workspace, 'manifest.json')
    inventory=json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else {}
    inherited=set(inventory.get('inherited_runs', []))
    runs={rid: run for rid, run in inventory.get('runs', {}).items() if rid not in inherited}
    files=[]
    for run_id, run in runs.items():
        for kind, path in [('code',run.get('input')),('stdout',run.get('stdout')),('stderr',run.get('stderr'))]:
            if path and workspace_path(workspace, path).is_file():files.append((kind,workspace.relative_to(root).as_posix()+'/'+path,run_id))
    for path in sorted((workspace/'outputs'/'intermediate').rglob('*')):
        meta=inventory.get('files', {}).get(path.relative_to(workspace).as_posix(), {})
        if path.is_file() and meta.get('source_run') not in inherited:
            files.append(('data',path.relative_to(root).as_posix(),meta.get('source_run') or ''))
    for kind,path,run_id in files:
        target=workspace_path(root, path)
        ref='artifact:'+aid+':'+hashlib.sha256(path.encode()).hexdigest()[:16]
        payload={'id':ref,'kind':'text','subproblem':eid,'name':target.name,
                 'value':'','run_id':run_id,'path':path,'unit':'','description':''}
        with target.open('rb') as stream:content_hash=hashlib.file_digest(stream,'sha256').hexdigest()
        state['artifacts'][ref]={'type':kind,'experiment_id':eid,'producer_attempt':aid,
            'payload':payload,'active':True,'content_hash':content_hash,'artifact_hash':content_hash}


def report_artifact(state, experiment):
    return state['artifacts'][experiment['report_ref']] if experiment['report_ref'] else None


def experiment_report(state, experiment):
    artifact=report_artifact(state, experiment)
    return artifact['report'] if artifact else {}


def experiment_design(state, experiment):
    execution=state['executions'].get(experiment['execution_id'])
    return (execution.get('result') or {}).get('design') if execution else None


def save_report_artifact(state, root, experiment, report):
    import json
    from .validation import fingerprint
    aid=experiment['execution_id'];version=fingerprint(report)
    ref='report:'+aid+':'+version[:16]
    path=f'outputs/research/{aid}/report-{version[:16]}.json'
    target=root/path;target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    previous=report_artifact(state,experiment) or {}
    state['artifacts'][ref]={**previous,'type':'report','experiment_id':experiment['task_id'],
        'producer_attempt':aid,'active':True,'report':report,'content_hash':version,
        'previous_ref':previous.get('previous_ref') if experiment['report_ref']==ref else experiment['report_ref'],
        'payload':{'id':ref,'kind':'text','subproblem':experiment['task_id'],'name':'实验报告',
                   'value':report.get('summary',''),'path':path,'run_id':'','unit':'','description':''}}
    if previous and previous.get('payload',{}).get('id')!=ref:
        previous['active']=False
    experiment['report_ref']=ref
