"""Report feedback is an artifact operation, not a new computational attempt."""

from .domain import experiment_report, experiment_design, report_artifact, save_report_artifact
from pydantic import Field
from .models import Contract, ResearchConflict
from .domain import findings
from .store import digest
from .validation import report_fingerprint

CLOSED = {'resolved', 'dismissed', 'superseded'}


def supplements(snapshot, issues):
    ids={i['id'] for i in issues}
    return [t for t in snapshot['experiments'].values() if not t['retired'] and
        t['acceptance']=='accepted' and t['validity']=='current' and ids.intersection(t.get('investigates',[]))]


def report_version(snapshot, task, issues):
    return digest([task['revision'],task['execution_id'],report_fingerprint(experiment_report(snapshot, task) or {}),
        sorted(i['id'] for i in issues),[(t['task_id'],t['execution_id']) for t in supplements(snapshot,issues)]])


def pending_report_review(store):
    snapshot = store.snapshot()
    for key, task in snapshot['experiments'].items():
        if task['retired'] or task['acceptance'] != 'accepted' or task['validity'] != 'current':continue
        issues = [i for i in findings(snapshot) if i['task_id'] == key and not i.get('section')
                  and i['source'] == 'local_review' and i['status'] not in CLOSED and i['action'] != 'recompute']
        if not issues:continue
        signature = report_version(snapshot,task,issues)
        previous=(report_artifact(snapshot,task) or {}).get('review') or {}
        if previous.get('input_version') != signature or (any(i['category']=='writing' for i in issues) and previous.get('attempt_count',0)<3):
            return {'task_id':key, 'version':signature}
    return None


class ReportEdit(Contract):
    summary: str = Field(min_length=1)
    sensitivity: str = ''
    limitations: list[str] = Field(default_factory=list)


async def repair_report(ctx, inputs):
    from ai_system.graph.llm_utils import call_structured, as_messages
    from ai_system.graph.tracing import labelled
    from ai_system.modules.experiments.review import review_solution, review_prompt, submission_issues
    from ai_system.modules.registry import CapabilityProduct
    store = ctx.research
    target = inputs
    snapshot = store.snapshot();task = snapshot['experiments'][target['task_id']]
    original = experiment_report(snapshot, task) or {};report = dict(original)
    issues = [i for i in findings(snapshot) if i['task_id'] == task['task_id'] and not i.get('section')
              and i['source'] == 'local_review' and i['status'] not in CLOSED and i['action'] != 'recompute']
    if report_version(snapshot,task,issues)!=target['version']:
        raise ResearchConflict('报告和意见版本已改变，请重新观察')
    attempt = snapshot['executions'][task['execution_id']]
    design = experiment_design(snapshot, task) or (attempt.get('result') or {}).get('design') or {}
    branch = {'source':snapshot['spec']['requirement'], 'subproblem':task['subproblem'],
        'data_profile':(snapshot.get('materials') or {}).get('profile',''),
        'design':design if design.get('chosen') else {'chosen':{'name':original.get('approach',''), 'idea':task.get('method_instruction','')}},
        'research_contract':{'requirements':[r for r in snapshot['spec']['requirements'] if r['id'] in task['requirement_ids']]},
        'experiment':task.get('experiment'), 'review_findings':issues}
    supplemental=supplements(snapshot,issues)
    branch['supplementary_evidence']=[{'task':t['subproblem'],'report':experiment_report(snapshot, t),
        'evidence':ctx.registry.to_prompt_table(t['task_id'])} for t in supplemental]
    run_ids = {e['payload']['run_id'] for e in snapshot['artifacts'].values()
               if e['producer_attempt'] in {task['execution_id'],*(t['execution_id'] for t in supplemental)}}
    runs = []
    for run_id in sorted(run_ids):
        info = ctx.fs.get_run_info(run_id)
        if not info or not info.get('input'):continue
        runs.append({'run_id':run_id,'ok':info.get('status')=='success','code':ctx.fs.read_text(info['input'])})
    # A text editor has no execution tools and cannot change methods, registered
    # values, claims or the immutable worker receipt.
    with labelled(task['task_id']+':report'):
        if any(i['category']!='service' for i in issues):
            edit = await call_structured(ctx.writer_llm, ReportEdit, as_messages(
                '根据报告意见修订summary、sensitivity、limitations。只修解释、方法说明、影响范围和局限；'
                '不得伪造实验、改变已登记数值或声称执行了新方法。真实计算问题保留为局限和待核查事项。'
                '修改后仍必须完整保留原报告的实质内容、引用和边界。',
                review_prompt(branch,report,ctx,runs)))
            report.update(edit.model_dump())
        errors = submission_issues(ctx,report,task['task_id'],task['dependencies'])
        if errors:raise ValueError('报告修订引用校验失败：'+'；'.join(errors))
        review = await review_solution(ctx,branch,report,runs)
    allowed = {ref for ref,e in snapshot['artifacts'].items() if e['active']} | {r for i in issues for r in i['evidence_refs']}
    if any(i.task_id!=task['task_id'] or not set(i.evidence_refs)<=allowed for i in review.issues):
        raise ValueError('报告复核引用超出当前对象与证据范围')
    with store._transaction() as db:
        state = store._read(db);current = state['experiments'][task['task_id']]
        if current['execution_id'] != task['execution_id'] or current['revision']!=task['revision'] or report_fingerprint(experiment_report(state, current) or {})!=report_fingerprint(original):
            raise ResearchConflict('报告修订期间计算或报告版本已改变')
        current_issues=[i for i in findings(state) if i['id'] in {j['id'] for j in issues}]
        if report_version(state,current,current_issues)!=target['version']:raise ResearchConflict('报告核查期间关联证据已改变')
        version = {'source_version':target['version'], 'input_version':target['version'], 'report_hash':report_fingerprint(report),
            'attempt_id':task['execution_id'], 'revision':len((report_artifact(state,current) or {}).get('versions',[]))+1,
            'original':original, 'report':report, 'issues':[i.model_dump() for i in review.issues],
            'reviewed_issue_ids':[i['id'] for i in issues],
            'attempt_count':((report_artifact(state,current) or {}).get('review') or {}).get('attempt_count',0)+1}
        save_report_artifact(state,store.root,current,report)
        artifact=report_artifact(state,current)
        artifact.setdefault('versions',[]).append(version)
        artifact['review']=version
        # The fresh review explicitly re-examines every targeted finding and
        # returns only unresolved findings. Preserve superseded wording as a
        # resolved historical record, then publish its actual remaining findings.
        for issue in findings(state):
            if issue['id'] in version['reviewed_issue_ids']:
                issue.update(status='resolved',resolution_attempt=task['execution_id'],
                    resolution_report=version['report_hash'])
        for issue in review.issues:
            recorded=store._record_issue(state,issue,'suspected',producer_attempt=task['execution_id'],source='local_review')
            recorded.update(status='suspected',resolution_attempt=None)
        # Bind unchanged unresolved findings to this reviewed output too, so
        # observe chooses a scientific next step instead of re-editing forever.
        remaining = sorted(i['id'] for i in findings(state) if i['task_id']==task['task_id'] and
            not i.get('section') and i['source']=='local_review' and i['status'] not in CLOSED and i['action']!='recompute')
        artifact['review']['input_version'] = report_version(state,current,
            [i for i in findings(state) if i['id'] in remaining])
        store._save(db,state,'report_reviewed',{'task_id':task['task_id'],'version':version['revision'],
            'report_hash':version['report_hash'],'remaining':remaining})
    from .control import public_snapshot
    await ctx.events.emit('research_state',public_snapshot(store.snapshot(),source_root=store.root))
    return CapabilityProduct('research_state',{'log':['report updated and reviewed without repeating computation']})


def register(registry):
    from ai_system.modules.registry import Inputs
    class Target(Inputs):
        task_id: str
        version: str
    registry.register('research','research.review_report','research_state',repair_report,Target)
