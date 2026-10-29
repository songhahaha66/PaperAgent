"""Explicit interpretation edits and advisory checks; neither runs computation.

The research/experiment owner decides what a semantic suspicion means. A second
model never silently edits facts, closes findings, or schedules another run.
"""
from copy import deepcopy
import re
from pydantic import Field
from .models import Contract, ResearchConflict
from .domain import experiment_report, report_artifact, save_report_artifact, findings, experiment_scope, current_result_refs
from .validation import report_fingerprint
from .store import digest

CLOSED = {'resolved', 'dismissed', 'superseded'}


class FindingEdit(Contract):
    statement: str = Field(min_length=1)
    strength: str = '倾向'


class ReportEdit(Contract):
    summary: str = Field(min_length=1)
    key_findings: list[FindingEdit]
    sensitivity: str = ''
    limitations: list[str] = Field(default_factory=list)


class FeedbackDecision(Contract):
    issue_id: str
    disposition: str = Field(pattern='^(adopt|dismiss|limitation)$')
    reason: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


def interpretation_version(snapshot, task):
    return digest([task['revision'], task['execution_id'], task['report_ref']])


def revise_interpretation(store, task_id, version, edit=None, decisions=()):
    """Atomically revise report + derived claims, preserving receipts and old views."""
    from ai_system.graph.registry import find_refs
    with store._transaction() as db:
        state = store._read(db)
        task = state['experiments'][task_id]
        if interpretation_version(state, task) != version:
            raise ResearchConflict('解释或计算版本已改变，请重新观察')
        if not task['report_ref'] or task['execution_status'] == 'running':
            raise ResearchConflict('只能修订已经提交的实验解释')
        scope = experiment_scope(state['experiments'], {task_id})
        allowed = current_result_refs(state, scope)
        supporting = {ref for ref,a in state['artifacts'].items() if a['experiment_id'] in scope}
        # A failed/unsupported attempt may still be interpreted as history, with
        # its own retained observations explicitly labelled as such.
        allowed |= {ref for ref, a in state['artifacts'].items()
                    if a['producer_attempt'] == task['execution_id'] and a['type'] == 'result'}
        original = deepcopy(experiment_report(state, task))
        report = {**original, **edit.model_dump()} if edit is not None else original
        texts = [report.get('summary', ''), report.get('sensitivity', ''), *report.get('limitations', []),
                 *(f['statement'] for f in report.get('key_findings', []))]
        if not set(find_refs(' '.join(texts))) <= allowed:
            raise ValueError('解释引用了未知、过期或无依赖的结果')
        by_id = {i['id']: i for i in findings(state)}
        for decision in decisions:
            issue = by_id.get(decision.issue_id)
            if not issue or issue['task_id'] != task_id or issue['status'] in CLOSED:
                raise ResearchConflict('意见已处理或不属于该实验')
            if not set(decision.evidence_refs) <= supporting:
                raise ValueError('处置意见引用了未知或作用范围外的证据')
            if issue.get('repair_targets'):
                raise ResearchConflict('确定性检查失败须按冻结契约修复，不能通过解释修订关闭')
            issue.setdefault('decisions', []).append({**decision.model_dump(), 'report_ref':task['report_ref']})
            issue.update(status={'adopt':'resolved','dismiss':'dismissed','limitation':'inconclusive'}[decision.disposition],
                         blocking=False, resolution_reason=decision.reason, resolution_attempt=task['execution_id'])
        if edit is not None:
            previous_ref = task['report_ref']
            save_report_artifact(state, store.root, task, report)
            state['artifacts'][previous_ref]['active'] = previous_ref == task['report_ref']
            # Historical claims are retained with their original text and evidence.
            for claim in state['claims'].values():
                if claim['experiment_id'] == task_id and claim.get('active', True):
                    claim.update(active=False, status='withdrawn', reason='实验解释已修订', superseded_by=task['report_ref'])
            for index, finding in enumerate(report.get('key_findings', [])):
                ref = 'C' + str(db.execute(
                    "INSERT INTO counters VALUES (?, 'C', 1) ON CONFLICT(research_id,prefix) DO UPDATE SET value=value+1 RETURNING value",
                    (store.research_id,)).fetchone()[0])
                # Older imports may not have populated the counter.
                while ref in state['claims']:
                    ref = 'C' + str(db.execute("UPDATE counters SET value=value+1 WHERE research_id=? AND prefix='C' RETURNING value", (store.research_id,)).fetchone()[0])
                state['claims'][ref] = {'payload':{'id':ref,'subproblem':task_id, **finding,
                    'evidence':list(dict.fromkeys(find_refs(finding['statement'])))},
                    'producer_attempt':task['execution_id'], 'experiment_id':task_id,
                    'active':True, 'status':'current', 'report_ref':task['report_ref'], 'assumption_refs':{}}
            for q in state['questions'].values():
                if q.get('judgment') and (q['id'] == task['question_id'] or set(q.get('artifact_refs', [])) & allowed):
                    q['answer_status'] = 'needs_update'
            state['revision'] += 1
            state['publication'] = None
            state['outcome'] = 'running'
        store._save(db, state, 'interpretation_revised', {'task_id':task_id, 'from':version,
                    'report_ref':task['report_ref'], 'decisions':[d.model_dump() for d in decisions]})
    return {'log':['实验解释与当前结论已更新，保留计算、旧观点及处置理由；未执行计算。']}


async def repair_report(ctx, inputs):
    from ai_system.modules.registry import CapabilityProduct
    edit = ReportEdit.model_validate(inputs['edit']) if inputs.get('edit') is not None else None
    result = revise_interpretation(ctx.research, inputs['task_id'], inputs['version'], edit,
                                  [FeedbackDecision.model_validate(d) for d in inputs['decisions']])
    from .control import refresh_registry
    refresh_registry(ctx)
    return CapabilityProduct('research_state', result)


async def check_report(ctx, inputs):
    """Return specific doubts for the owner to verify, accept, reject or retain."""
    from ai_system.modules.registry import CapabilityProduct
    from ai_system.modules.experiments.review import review_solution
    from .control import experiment_dispatch
    snapshot = ctx.research.snapshot(); task = snapshot['experiments'][inputs['task_id']]
    if interpretation_version(snapshot, task) != inputs['version']:
        raise ResearchConflict('核查目标版本已改变')
    report = experiment_report(snapshot, task)
    runs = []
    for a in snapshot['artifacts'].values():
        if a['experiment_id'] != inputs['task_id'] or a['type'] != 'code':continue
        info = ctx.fs.get_run_info(a['payload']['run_id']) or {}
        path = a['payload'].get('path')
        if path:runs.append({'run_id':a['payload']['run_id'], 'ok':info.get('status')=='success', 'code':ctx.fs.read_text(path)})
    review = await review_solution(ctx, experiment_dispatch(ctx, inputs['task_id']), report, runs)
    scope=experiment_scope(snapshot['experiments'],{inputs['task_id']})
    supporting={ref for ref,a in snapshot['artifacts'].items() if a['experiment_id'] in scope}
    if any(i.task_id!=inputs['task_id'] or not set(i.evidence_refs)<=supporting for i in review.issues):
        raise ValueError('核查意见引用了未知或作用范围外的实验/证据')
    with ctx.research._transaction() as db:
        state = ctx.research._read(db)
        if interpretation_version(state, state['experiments'][inputs['task_id']]) != inputs['version']:
            raise ResearchConflict('核查期间解释已改变')
        for report in review.issues:
            issue = ctx.research._record_issue(state, report, 'suspected', producer_attempt=task['execution_id'], source='semantic_check')
            issue['blocking'] = False
        ctx.research._save(db, state, 'semantic_check', {'task_id':inputs['task_id'], 'version':inputs['version'],
                                  'issues':[i.model_dump() for i in review.issues]})
    return CapabilityProduct('research_state', {'findings':[i.model_dump() for i in review.issues],
                       'log':['语义核查仅提供待核实疑点；未修改解释或计算。']})


async def read_artifact(ctx, inputs):
    import hashlib
    from ai_system.modules.registry import CapabilityProduct
    state=ctx.research.snapshot()
    path=inputs['path']
    known=({a['payload'].get('path') for a in state['artifacts'].values()} | set(ctx.research.file_inputs())
           if ctx.fs.root==ctx.research.root else set())
    known.update(path for path,meta in ctx.fs._manifest.get('files',{}).items() if meta.get('visibility')=='user')
    known.update(info[field] for info in ctx.fs._manifest.get('runs', {}).values()
                 for field in ('input', 'stdout', 'stderr') if info.get(field))
    if re.fullmatch(r'\.system/context-outputs/[a-f0-9]+\.json', path):known.add(path)
    if path not in known:raise ValueError('只能读取当前原始资料或已保存的工作区文件')
    from services.file_services.workspace_layout import workspace_path
    target=ctx.fs.canonical_artifact(path) if path.startswith('outputs/research/') else ctx.fs.abs(path)
    if not target.is_file():raise ValueError('资料文件不可用')
    def read():
        raw=target.read_bytes()
        if target.suffix.lower()=='.pdf':
            import pdfplumber
            with pdfplumber.open(target) as pdf:
                text='\n\n'.join(f'第{i+1}页\n'+(page.extract_text() or '本页无可提取文本') for i,page in enumerate(pdf.pages))
        elif target.suffix.lower() in {'.xlsx','.xls'}:
            import pandas as pd
            book=pd.ExcelFile(target)
            with book:
                text='\n\n'.join('工作表：'+sheet+'\n'+book.parse(sheet,nrows=40).to_string(index=False) for sheet in book.sheet_names)
            text+='\n（每张工作表仅预览前40行；完整计算请直接读取原文件。）'
        else:
            try:text=raw.decode('utf-8')
            except UnicodeError:raise ValueError('此二进制文件无法直接提取文本，请使用其真实格式读取')
        return raw,text
    import asyncio
    raw,text=await asyncio.to_thread(read)
    start=inputs['start'];limit=inputs['limit']
    return CapabilityProduct('research_state',{'path':path,'version':hashlib.sha256(raw).hexdigest(),
        'total_chars':len(text),'excerpt':text[start:start+limit]})


def register(registry):
    from ai_system.modules.registry import Inputs
    class Target(Inputs):
        task_id: str
        version: str
    class EditTarget(Target):
        edit: ReportEdit | None = None
        decisions: list[FeedbackDecision] = Field(default_factory=list)
    class ReadTarget(Inputs):
        task_id: str | None = None
        path: str
        start: int = Field(default=0, ge=0)
        limit: int = Field(default=12000, ge=1, le=24000)
    registry.register('research','research.read_artifact','research_state',read_artifact,ReadTarget,
        model_callable=True, purpose='读取已保存的原始资料或实验产物文本，返回真实内容和版本；无需重新计算。')
    registry.register('research','research.revise_interpretation','research_state',repair_report,EditTarget,
        purpose='按当前interpretation_version修订历史实验解释。')
    registry.register('research','research.check_interpretation','research_state',check_report,Target,
        purpose='针对历史实验核查代码、输出和解释。')
