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
        if eid!=task_id and (experiment['kind']=='verify' or experiment['execution_status']!='succeeded'):continue
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
    workspace=root if execution.get('workspace')=='.' else root/'experiments'/aid/'workspace'
    manifest=workspace_path(workspace, 'manifest.json')
    inventory=json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else {}
    inherited=set(inventory.get('inherited_runs', []))
    runs={rid: run for rid, run in inventory.get('runs', {}).items()
          if rid not in inherited and ('run_ids' not in execution or rid in execution['run_ids'])}
    relative=lambda path: (workspace.relative_to(root)/path).as_posix()
    files=[]
    for run_id, run in runs.items():
        for kind, path in [('code',run.get('input')),('stdout',run.get('stdout')),('stderr',run.get('stderr'))]:
            if path and workspace_path(workspace, path).is_file():files.append((kind,relative(path),run_id))
    # Execution outputs can live anywhere in the public workspace. Index the
    # delivered manifest, not one historical output directory, and retain the
    # archived version so later edits cannot change the evidence being read.
    for path, meta in sorted(inventory.get('files', {}).items()):
        run_id = meta.get('source_run')
        if run_id not in runs:
            continue
        archived = meta.get('archive_path')
        if archived:
            if not archived.startswith(f'runs/{run_id}/artifacts/'):
                raise ValueError('实验产物归档路径与运行不匹配')
            path = archived
        elif workspace_path(workspace,f'runs/{run_id}/artifacts/{path}').is_file():
            path = f'runs/{run_id}/artifacts/{path}'
        target = workspace_path(workspace, path)
        if target.is_file():
            kind = 'code' if target.suffix == '.py' else 'data'
            files.append((kind, relative(path), run_id))
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


def _retain_report(root, attempt_id, version, report):
    import json
    from services.file_services.file_helper import atomic_write
    target=workspace_path(root,f'.system/reports/{attempt_id}/report-{version[:16]}.json')
    raw=json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8')
    if target.exists():
        if target.read_bytes()!=raw:raise ValueError('实验报告归档校验失败')
    else:
        atomic_write(target,lambda stream:stream.write(raw))
    return target


def retained_report(root, path):
    """Resolve host-authored reports from committed state, never a mutable copy.

    Reports have no execution run. Older reports can reconstruct their archive
    from the committed JSON without inventing a sandbox receipt or trusting files.
    """
    import json,sqlite3
    from contextlib import closing
    from .validation import fingerprint
    database=workspace_path(root,'.system/research.sqlite')
    if database.is_file():
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='research'").fetchone():
                for (payload,) in db.execute('SELECT payload FROM research'):
                    for artifact in json.loads(payload).get('artifacts',{}).values():
                        if artifact.get('type')!='report' or artifact.get('payload',{}).get('path')!=path:continue
                        report=artifact['report'];version=fingerprint(report);aid=artifact['producer_attempt']
                        if artifact['content_hash']!=version or path!=f'outputs/research/{aid}/report-{version[:16]}.json':
                            raise ValueError('实验报告来源不匹配')
                        return _retain_report(root,aid,version,report)
    raise ValueError('正式成果缺少归档映射')


def save_report_artifact(state, root, experiment, report):
    import json
    from .validation import fingerprint
    aid=experiment['execution_id'];version=fingerprint(report)
    ref='report:'+aid+':'+version[:16]
    path=f'outputs/research/{aid}/report-{version[:16]}.json'
    _retain_report(root,aid,version,report)
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
