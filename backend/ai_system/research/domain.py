"""Question → experiment → artifact. Only ResearchStore owns research state."""
from itertools import chain

SCHEMA_VERSION = 4
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


def current_result_refs(state, experiment_ids):
    """Usable evidence is distinct from whether a research question is answered."""
    executions = {key: experiment['execution_id'] for key, experiment in state['experiments'].items()
                  if key in experiment_ids and not experiment['retired'] and experiment['kind'] != 'verify'
                  and experiment['acceptance'] == 'accepted' and experiment['validity'] == 'current'}
    return {ref for ref, artifact in state['artifacts'].items()
            if artifact['experiment_id'] in executions and artifact['type'] == 'result' and artifact['active']
            and artifact['producer_attempt'] == executions[artifact['experiment_id']]}


def attach_question(state, experiment, *, question_id=None, statement=None, title=None):
    """Attach once, including imported experiments. A method never owns a question."""
    questions = state['questions']
    if question_id is None:
        question_id = 'question:' + experiment['task_id']
        questions[question_id] = {'id': question_id, 'title': title or statement or experiment['objective'],
            'question': statement or experiment['objective'], 'judgment': '', 'artifact_refs': [],
            'limitations': [], 'next_step': '', 'judgments': []}
    experiment['question_id'] = question_id
    return questions[question_id]


def register_execution_artifacts(state, root, execution):
    """Index actual retained files; source ownership comes from the execution workspace."""
    import hashlib
    import json
    aid=execution['attempt_id'];eid=execution['task_id']
    workspace=root/'.system'/'attempts'/aid/'workspace'
    manifest=workspace/'manifest.json'
    runs=json.loads(manifest.read_text(encoding='utf-8'))['runs'] if manifest.is_file() else {}
    files=[]
    for run_id, run in runs.items():
        for kind, path in [('code',run.get('input')),('stdout',run.get('stdout')),('stderr',run.get('stderr'))]:
            if path and (root/path).is_file():files.append((kind,path,run_id))
    for path in sorted((workspace/'outputs'/'intermediate').rglob('*')):
        if path.is_file():files.append(('data',path.relative_to(root).as_posix(),''))
    for kind,path,run_id in files:
        target=(root/path).resolve()
        if not target.is_relative_to(root):raise ValueError('实验产物超出项目目录')
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
    experiment['report_ref']=ref
