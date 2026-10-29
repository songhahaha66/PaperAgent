"""Adapters between checkpoint execution and committed research state."""
from services.file_services.workspace_layout import workspace_path

from .domain import experiment_report, experiment_design

from ai_system.research.domain import findings as research_findings
import asyncio
import json
import shutil
import hashlib


from ..graph.registry import Claim, Result
from ..graph.context import GraphContext
from .models import WorkerResult, ResearchConflict, IssueReport
from .planner import ready_tasks
from .assumptions import assumption_scope
from .progress import task_progress


def refresh_registry(ctx):
    payload = ctx.research.registry_payload()
    ctx.registry.results = {r["id"]: Result(**r) for r in payload["results"]}
    ctx.registry.claims = {c["id"]: Claim(**c) for c in payload["claims"]}
    ctx.registry.save()


async def observe(state, runtime):
    ctx = runtime.context
    store = ctx.research
    if store.snapshot() is None:
        raise ValueError("研究状态不存在，不能从 checkpoint 恢复业务数据")
    snapshot = store.refresh_inputs()
    from ai_system.modules.experiments.execution import recover_outputs
    try:
        await recover_outputs(ctx)
    finally:
        await ctx.kernels.close()  # Recovery must release the lease before compilation.
    if ctx.experience_scope is not None:
        try:
            from ai_system.modules.registry import invoke
            await invoke(ctx, 'experience.candidates')
        except Exception:
            await ctx.events.stage('经验索引暂不可用，研究继续；可在经验面板补建')
    ctx.skill_policy = store.skill_preferences()['policy']
    refresh_registry(ctx)
    if 'materials' not in snapshot:
        with store._transaction() as db:
            saved=store._read(db)
            saved['materials']={'inputs':store.file_inputs(),'profile':''}
            store._save(db,saved,'materials_profiled',saved['materials'])
    from .loop import prepare_action
    blocked = await prepare_action(ctx)
    snapshot = store.snapshot()
    refresh_registry(ctx)
    if blocked:
        store.set_outcome('blocked')
        from .planner import decision_block_message
        await ctx.set_stage('solve','blocked',decision_block_message(store,snapshot))
    elif snapshot['outcome'] not in {'completed','needs_attention'}:
        store.set_outcome('running')
    snapshot = store.snapshot()
    await ctx.events.emit('research_state', public_snapshot(snapshot,source_root=ctx.research.root))
    return {'research_blocked': blocked}


def graph_view(snapshot, root=None):
    from .views import questions
    publication=snapshot['publication'] or {}
    source=snapshot['spec']['requirement']
    view = {'task':source, 'analysis':{
        'title':publication.get('title') or source.splitlines()[0][:80],
        'restatement':source, 'overall_analysis':'',
        'subproblems':[t['subproblem'] for t in snapshot['experiments'].values() if t['kind']!='verify' and not t['retired']],
        'questions':questions(snapshot), 'requirements':snapshot['spec']['requirements'],
        'assumptions':[a for a in snapshot['assumptions'].values() if a['status']!='retired']},
        'solutions':{key:experiment_report(snapshot, t) for key,t in snapshot['experiments'].items()
                     if t['acceptance']=='accepted' and t['validity']=='current' and not t['retired']},
        'designs':{key:experiment_design(snapshot, t) for key,t in snapshot['experiments'].items() if experiment_design(snapshot, t)},
        'sections':{} if snapshot['spec'].get('output_mode')=='latex' else {key:s['text'] for key,s in snapshot['sections'].items() if s['validity']=='current'},
        'section_contexts':{} if snapshot['spec'].get('output_mode')=='latex' else {key:s.get('context_hash') for key,s in snapshot['sections'].items() if s['validity']=='current'},
        'data_profile':snapshot['materials']['profile'],
        'outline':publication.get('outline',[]), 'consistency':snapshot.get('consistency',[]),
        'repair_round':publication.get('repairs',0),
        'issues':[i for i in snapshot['feedback'] if i.get('section') and i['action']=='rewrite'
                  and i['status'] not in {'resolved','dismissed','superseded'}]}

    if snapshot.get('document') and root is not None:
        import hashlib
        path = root / snapshot['document']['path']
        text = path.read_text(encoding='utf-8') if path.is_file() else ''
        view.update(document=snapshot['document'],sections={'document':text},
                    source_version=hashlib.sha256(text.encode()).hexdigest())
    return view


def paper_state(store):
    """Saved draft/history text; publication validity remains in the Store."""
    snapshot=store.snapshot()
    state=graph_view(snapshot)
    state['sections']={key:s['text'] for key,s in snapshot['sections'].items()}
    state['section_contexts']={key:s.get('context_hash') for key,s in snapshot['sections'].items()}
    from services.chat_services.paper_project import is_latex,source_state
    return source_state(store,state) if is_latex(store) else state


def experiment_dispatch(ctx, key):
    """Workers receive system-built context, never a model-supplied branch."""
    snapshot=ctx.research.snapshot()
    task=snapshot['experiments'][key]
    from ai_system.modules.experiments.store import ExperimentStore
    comparison=ExperimentStore(ctx.research).read(key,task['revision']) if (task.get('experiment') or {}).get('csv') else None
    return {'resume_comparison':bool(comparison and comparison.get('recovery_requested')),'source':task.get('source') or snapshot['spec']['requirement']+'\n'+'\n'.join(snapshot['spec']['amendments']), 'subproblem':task['subproblem'],
        'task_revision':task['revision'], 'experiment':task.get('experiment'),
        'data_profile':snapshot['materials']['profile'], 'feedback':task.get('method_instruction',''),
        'clarifications':snapshot.get('clarifications',[]),
        'continuation':task.get('continuation'),
        'previous_report':experiment_report(snapshot,task) if task.get('continuation') else None,
        'research_contract':{'requirements':[r for r in snapshot['spec']['requirements'] if r['id'] in task['requirement_ids']],
            'task_ids':list(snapshot['experiments']), 'assumption_task_ids':assumption_scope(snapshot['experiments'],key),
            'assumptions':[snapshot['assumptions'][aid] for aid in task['assumption_ids']], 'checks':[]},
        'design':experiment_design(snapshot, task),
        'review_findings':[i for i in snapshot['feedback'] if i['status'] not in {'resolved','dismissed','superseded'} and i['task_id']==key],
        'dependencies':{dep:experiment_report(snapshot,snapshot['experiments'][dep]) for dep in task['dependencies']}}


def public_snapshot(snapshot, *, work_status=None, source_root=None):
    ready = set(ready_tasks(snapshot))
    from .views import questions,evidence_uses,execution,delivery_summary
    stages=snapshot.get('delivery_stages',{})
    if snapshot['spec'].get('output_mode')=='latex' and source_root is not None:
        from pathlib import Path
        from services.chat_services.paper_project import snapshot as source_snapshot
        path=workspace_path(Path(source_root), '.system/validation.json')
        checked=json.loads(path.read_text()) if path.exists() else {}
        if checked.get('source_version')!=source_snapshot(source_root)['version']:
            stages={k:({**stage,'status':'pending','note':'源码已更新，需检查当前版本'}
                       if k in {'validate','render','export'} and stage['status']=='completed' else stage)
                    for k,stage in stages.items()}
    stages = {key: {**stage, 'status': 'interrupted' if stage['status'] == 'in_progress' and
        work_status in {'paused', 'cancelled', 'failed'} else stage['status']}
        for key, stage in stages.items()}
    return {key: snapshot[key] for key in ("engine_version", "schema_version", "research_id", "revision", "commit_seq", "outcome")} | {
        "questions": questions(snapshot), "evidence_uses":evidence_uses(snapshot,source_root),
        "runtime":execution(snapshot,work_status),
        "delivery": delivery_status(snapshot),
        "delivery_summary": delivery_summary(snapshot, stages),
        "delivery_stages": stages,
        "experiments": [public_task(snapshot, task, ready, work_status) for task in snapshot["experiments"].values() if not task["retired"]],
        "feedback": [i for i in snapshot['feedback'] if i['status'] not in {'resolved', 'dismissed'}],
        "faults": [i for i in snapshot['faults'] if i['status'] not in {'resolved', 'dismissed', 'superseded'}],
        "requirements": [{**r,"status":requirement_status(snapshot,r)} for r in snapshot['spec']['requirements']],
        "assumptions":list(snapshot['assumptions'].values()),
    }


def requirement_status(snapshot, requirement):
    """An old accepted execution does not certify a later requirement version."""
    if snapshot['outcome']!='completed':return 'pending'
    for eid in requirement['task_ids']:
        task=snapshot['experiments'][eid]
        if task['retired'] or task['acceptance']!='accepted' or task['validity']!='current':continue
        attempt=snapshot['executions'].get(task['execution_id']) or {}
        frozen=attempt.get('experience_basis',{}).get('requirements',[])
        if any(r['id']==requirement['id'] and r.get('revision',1)==requirement.get('revision',1) for r in frozen):
            return 'accepted'
    return 'pending'


def public_task(snapshot, task, ready=None, work_status=None):
    result = {key: task.get(key) for key in ("task_id", "question_id", "revision", "objective", "dependencies",
              "execution_status", "acceptance", "validity", "reason", "kind")}
    result["validation"] = current_validation(snapshot, task)
    result["progress"] = task_progress(snapshot, task, set(ready_tasks(snapshot)) if ready is None else ready, work_status)
    return result


def current_validation(snapshot, task):
    # Rejected work is invalidated immediately; retain its verdict for this
    # revision, but never label an older revision's checks as current.
    attempt = next((a for a in reversed(snapshot["executions"].values())
                    if a["task_id"] == task["task_id"] and a["revision"] == task["revision"]), {})
    return attempt.get("validation")


from .questions import delivery_status


def _copy_tree(source, target):
    if source.exists():
        if any(p.is_symlink() for p in source.rglob("*")):
            raise ValueError("执行输入包含工作区外的文件链接")
        shutil.copytree(source, target, dirs_exist_ok=True)


def worker_context(ctx, order):
    """Root execution in an attempt workspace instead of active outputs.

    Inputs and predecessor handoffs are copied before execution. The submission
    checks source data hashes again, catching a correction during the copy/run.
    """
    work = ctx.workspace / "experiments" / order.attempt_id / "workspace"
    work.mkdir(parents=True, exist_ok=True)
    snapshot = ctx.research.snapshot()
    task = snapshot['experiments'][order.task_id]
    if task.get('input_files') is None:
        for folder in ('data', 'attachment'):
            _copy_tree(ctx.workspace / folder, work / folder)
    else:
        for path in task['input_files']:
            source = ctx.fs.abs(path)
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    from .domain import experiment_scope, execution_lineage
    from ai_system.sandbox.artifacts import inherit_execution_files
    from services.file_services.workspace_fs import WorkspaceFS
    scope = experiment_scope(snapshot['experiments'], task['dependencies'])
    target_fs = WorkspaceFS(str(work))
    target_fs.init_structure()
    inherited = set()
    # Dependency order first; continuation products override their older inputs.
    copied = set()
    def copy_execution(key, execution_id):
        for aid in reversed(execution_lineage(snapshot, execution_id)):
            if aid in copied: continue
            copied.add(aid)
            attempt=snapshot['executions'][aid]
            source = ctx.workspace if attempt.get('workspace')=='.' else ctx.workspace / 'experiments' / aid / 'workspace'
            inherited.update(inherit_execution_files(source, target_fs, key,run_ids=attempt.get('run_ids')))
    def copy_dependency(key):
        for dependency in snapshot['experiments'][key]['dependencies']:
            copy_dependency(dependency)
        copy_execution(key, snapshot['experiments'][key]['execution_id'])
    for dep in task['dependencies']:
        copy_dependency(dep)
    parent = snapshot['executions'][order.attempt_id].get('parent_attempt')
    if parent:
        copy_execution(order.task_id, parent)
    target_fs._manifest['inherited_runs'] = sorted(inherited)
    target_fs._save_manifest()
    local = GraphContext.create(str(work), planner_llm=ctx.planner_llm, coder_llm=ctx.coder_llm,
        writer_llm=ctx.writer_llm, stream_manager=ctx.events.stream_manager,
        template_requirements=ctx.template_requirements, skill_policy=ctx.skill_policy,
        experience_scope=ctx.experience_scope,experience_store=ctx.research,experience_order=order,
        source_workspace=ctx.workspace, sandbox_work_id=str(ctx.workspace),
        sandbox_experiment_id=order.task_id + ':' + str(order.revision) + ':' +
            hashlib.sha256(json.dumps({p: h for p,h in order.input_refs.items() if p != 'continuation'}, sort_keys=True).encode()).hexdigest())
    local.compact_paper=ctx.compact_paper
    local.delivery_fs = ctx.fs
    local.delivery_events = ctx.events
    allowed = scope | ({order.task_id} if parent else set())
    local.registry.results = {ref: result for ref, result in ctx.registry.results.items() if result.subproblem in allowed}
    local.registry.claims = {ref: claim for ref, claim in ctx.registry.claims.items() if claim.subproblem in allowed}
    # Published figures use root-workspace paths, while a continued attempt's
    # copied files still use their original attempt paths. Materialize the
    # registered paths too, preserving references and execution provenance.
    for result in local.registry.results.values():
        if result.path:
            source = ctx.fs.abs(result.path)
            if source.is_file():
                target = local.fs.abs(result.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    # Attempt-relative paths do not exist in the user's root workspace. Publish
    # their final paths only after the coordinator accepts the commit below.
    local.events.publish_files = False
    # Progress belongs to the coordinator. Attempts only emit local messages.
    local.set_stage = ctx.set_stage
    return local


async def execute(branch, runtime):
    from ai_system.modules.experiments.execution import solve
    from types import SimpleNamespace

    ctx = runtime.context
    key = branch["subproblem"]["id"]
    try:
        task=ctx.research.snapshot()['experiments'][key]
        if branch.get('task_revision',task['revision'])!=task['revision'] or branch['subproblem']!=task['subproblem']:
            raise ResearchConflict('行动所依据的研究版本已改变，请重新观察')
        if branch.get('experiment')!=task.get('experiment'):
            raise ResearchConflict('候选方法或评价条件与当前任务记录不符，请重新观察')
        order = ctx.research.claim(key)
    except ResearchConflict as exc:
        await ctx.events.stage(str(exc))
        return {'_execution_outcome': {'status': 'interrupted', 'message': str(exc)}}
    await ctx.events.emit("research_state", public_snapshot(ctx.research.snapshot(),source_root=ctx.research.root))
    local = None
    receipt = None
    captured_paths = None
    next_file_event = 0

    async def publish_outputs():
        nonlocal captured_paths, next_file_event
        if captured_paths is None:
            from ai_system.sandbox.artifacts import capture_run_files
            captured_paths = capture_run_files(local.fs, ctx.fs, run_ids=[
                rid for rid in local.fs._manifest['runs']
                if rid not in local.fs._manifest.get('inherited_runs', [])])
        # Resume interrupted notifications without capturing the same files again.
        while next_file_event < len(captured_paths):
            await ctx.events.file_changed(captured_paths[next_file_event])
            next_file_event += 1

    try:
        prepared=branch.get('design') or {}
        if prepared.get('experience_refs'):
            if prepared.get('experience_files',ctx.research.file_inputs())!=ctx.research.file_inputs():
                raise ResearchConflict('确认前后数据改变，请重新设计')
            ctx.research.bind_experiences(order,prepared['experience_refs'])
        frozen=ctx.research.snapshot()['executions'][order.attempt_id]['experience_basis']
        branch={**branch,'research_contract':{**branch['research_contract'],
                'requirements':frozen['requirements'],'checks':[]}}
        local = worker_context(ctx, order)
        solved = await solve(branch, SimpleNamespace(context=local))
        report,design = solved['report'],solved['design']
        if task.get('experiment') and design.get('experiment')!=task['experiment']:
            raise ResearchConflict('执行报告中的候选方法或评价条件与冻结任务不符')
        # Execution status describes completion, never scientific correctness.
        # Code, stdout/stderr and output files are archived independently of prose.
        await publish_outputs()
        report['files'] = list(captured_paths or [])
        result = WorkerResult(order=order,
            execution_status="succeeded" if report["status"] == "completed" else "failed",
            acceptance="accepted" if report["status"] == "completed" else "inconclusive",
            report=report, design=design,
            issues=([IssueReport(task_id=key, category="execution", message=report.get("error") or "本轮执行未完成")]
                    if report["status"] != "completed" else [])
                + [IssueReport.model_validate(i) for i in report.get("research_issues", [])])
        receipt = ctx.research.commit(result)
        refresh_registry(ctx)
        snapshot = ctx.research.snapshot()
        committed = snapshot["experiments"][key]
        await ctx.events.emit("research_state", public_snapshot(snapshot,source_root=ctx.research.root))
        accepted = (receipt["published"] and committed["execution_id"] == order.attempt_id and committed["acceptance"] == "accepted"
                    and committed["validity"] == "current")
        if accepted:
            code = local.workspace / "code" / f"{key}_solution.py"
            if code.exists():
                ctx.fs.write_text(f"code/{key}_solution.py", code.read_text(encoding="utf-8"))
        clear = accepted and not any(i["task_id"] == key and i["blocking"] and i["status"] not in {"resolved", "dismissed", "superseded"}
                                     for i in research_findings(snapshot))
        if clear:
            await ctx.events.emit("paper_solved", f"{key} 执行结束，结果文件与代码已保存")
        else:
            await ctx.events.stage(f"{key} 已提交，{receipt.get('reason') or '本轮未完成，请查看执行记录'}")
        return {"log": [f"commit {key}: {receipt}"], '_execution_outcome': {
            'status': 'completed' if result.execution_status == 'succeeded' else 'failed',
            'message': report.get('error')}}
    except asyncio.CancelledError:
        raise  # Recovery records cancellation and acquires a new lease.
    except Exception as exc:
        if receipt is not None:
            await ctx.events.stage(f"{key} 已提交，结果视图更新失败，将在恢复时重建：{exc}")
            return {"log": [f"projection {key} failed after commit: {exc}"], '_execution_outcome': {
                'status': 'completed' if result.execution_status == 'succeeded' else 'failed',
                'message': report.get('error')}}
        ctx.research.commit(WorkerResult(order=order, execution_status="failed", acceptance="inconclusive",
            report={"status": "failed", "error": str(exc)}, issues=[IssueReport(task_id=key, category="execution", message=str(exc))]))
        await ctx.events.emit("research_state", public_snapshot(ctx.research.snapshot(),source_root=ctx.research.root))
        await ctx.events.stage(f"{key} 执行失败：{exc}")
        return {"log": [f"attempt {key} failed: {exc}"], '_execution_outcome': {'status': 'failed', 'message': str(exc)}}
    finally:
        if local:
            try:
                # Code output is available even when later interpretation fails.
                # Publication of a file does not accept the experiment's conclusions.
                await publish_outputs()
            finally:
                await local.kernels.close()
