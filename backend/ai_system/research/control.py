"""Adapters between checkpoint execution and committed research state."""

from .domain import experiment_report, experiment_design

from ai_system.research.domain import findings as research_findings
import asyncio
import json
import shutil
from dataclasses import asdict
from pathlib import Path

from langgraph.types import Send

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
    store.initialize(state["task"], output_mode=ctx.output_mode)
    snapshot = store.refresh_inputs()
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
            saved['materials']={'inputs':store.file_inputs(),'profile':state.get('data_profile','') if not saved['executions'] else ''}
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
    view = graph_view(snapshot)
    # Checkpoint reducers need explicit tombstones for invalidated projections.
    for name in ('solutions','designs','sections'):
        view[name].update({key:None for key in state.get(name,{}) if key not in view[name]})
    return {**view, 'research_blocked':blocked, 'research_restart':False,
            'log':[f"observe: commit {snapshot['commit_seq']}"]}


def graph_view(snapshot):
    from .views import questions
    publication=snapshot['publication'] or {}
    source=snapshot['spec']['requirement']
    return {'task':source, 'analysis':{
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
        'outline':publication.get('outline',[]), 'consistency':[],
        'repair_round':publication.get('repairs',0),
        'issues':[i for i in snapshot['feedback'] if i.get('section') and i['action']=='rewrite'
                  and i['status'] not in {'resolved','dismissed','superseded'}]}


def experiment_dispatch(ctx, key):
    """Workers receive system-built context, never a model-supplied branch."""
    snapshot=ctx.research.snapshot()
    task=snapshot['experiments'][key]
    from .validation import protocol
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
            'assumptions':[snapshot['assumptions'][aid] for aid in task['assumption_ids']], 'checks':protocol(snapshot,key)},
        'design':experiment_design(snapshot, task),
        'review_findings':[i for i in snapshot['feedback'] if i['status'] not in {'resolved','dismissed','superseded'} and i['task_id']==key],
        'dependencies':{dep:experiment_report(snapshot,snapshot['experiments'][dep]) for dep in task['dependencies']}}


def public_snapshot(snapshot, *, work_status=None, source_root=None):
    ready = set(ready_tasks(snapshot))
    from .views import questions,evidence_uses,execution
    stages=snapshot.get('delivery_stages',{})
    if snapshot['spec'].get('output_mode')=='latex' and source_root is not None:
        from pathlib import Path
        from services.chat_services.paper_project import snapshot as source_snapshot
        path=Path(source_root)/'.system/validation.json'
        checked=json.loads(path.read_text()) if path.exists() else {}
        if checked.get('source_version')!=source_snapshot(source_root)['version']:
            stages={k:({**stage,'status':'pending','note':'源码已更新，需检查当前版本'}
                       if k in {'validate','render','export'} and stage['status']=='completed' else stage)
                    for k,stage in stages.items()}
    return {key: snapshot[key] for key in ("engine_version", "schema_version", "research_id", "revision", "commit_seq", "outcome")} | {
        "questions": questions(snapshot), "evidence_uses":evidence_uses(snapshot,source_root),
        "runtime":execution(snapshot,work_status),
        "delivery": delivery_status(snapshot),
        "delivery_stages": {key: {**stage, "status": "interrupted" if stage["status"] == "in_progress" and
            work_status in {"paused", "cancelled", "failed"} else stage["status"]}
            for key, stage in stages.items()},
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
        if any(p.is_symlink() and not p.resolve().is_relative_to(source.resolve()) for p in source.rglob("*")):
            raise ValueError("执行输入包含工作区外的文件链接")
        shutil.copytree(source, target, dirs_exist_ok=True)


def worker_context(ctx, order):
    """Root execution in an attempt workspace instead of active outputs.

    Inputs and predecessor handoffs are copied before execution. The submission
    checks source data hashes again, catching a correction during the copy/run.
    """
    work = ctx.workspace / ".system" / "attempts" / order.attempt_id / "workspace"
    work.mkdir(parents=True, exist_ok=True)
    for folder in ("data", "attachment"):
        _copy_tree(ctx.workspace / folder, work / folder)
    snapshot = ctx.research.snapshot()
    copied = set()

    def copy_dependency(key):
        if key in copied:
            return
        task = snapshot["experiments"][key]
        for dep in task["dependencies"]:
            copy_dependency(dep)
        copied.add(key)
        source = ctx.workspace / ".system" / "attempts" / task["execution_id"] / "workspace"
        _copy_tree(source / "outputs" / "intermediate", work / "outputs" / "intermediate")
        _copy_tree(source / "code", work / "code")

    for dep in snapshot["experiments"][order.task_id]["dependencies"]:
        copy_dependency(dep)
    parent=snapshot['executions'][order.attempt_id].get('parent_attempt')
    if parent:
        # Continue from saved products. Never replay the old code to recreate a kernel.
        previous=ctx.workspace/'.system'/'attempts'/parent/'workspace'
        for folder in ('outputs','code','runs'):
            _copy_tree(previous/folder,work/folder)
        if (previous/'manifest.json').is_file():
            shutil.copyfile(previous/'manifest.json',work/'manifest.json')
    local = GraphContext.create(str(work), planner_llm=ctx.planner_llm, coder_llm=ctx.coder_llm,
        writer_llm=ctx.writer_llm, judge=ctx.judge, stream_manager=ctx.events.stream_manager,
        max_solver_steps=ctx.max_solver_steps, max_review_rounds=ctx.max_review_rounds,
        template_requirements=ctx.template_requirements, check_claims=ctx.check_claims, skill_policy=ctx.skill_policy,
        experience_scope=ctx.experience_scope,experience_store=ctx.research,experience_order=order,
        source_workspace=ctx.workspace)
    local.compact_paper=ctx.compact_paper
    local.registry.results = dict(ctx.registry.results)
    local.registry.claims = dict(ctx.registry.claims)
    local.registry.allocate_id = ctx.research.allocate_ref
    # Attempt-relative paths do not exist in the user's root workspace. Publish
    # their final paths only after the coordinator accepts the commit below.
    local.events.publish_files = False
    # Progress belongs to the coordinator. Attempts only emit local messages.
    local.set_stage = ctx.set_stage
    return local


async def execute(branch, runtime):
    from ai_system.modules.experiments.execution import solve
    from ai_system.modules.experiments.review import report_issues, needs_figure
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
        return {}
    await ctx.events.emit("research_state", public_snapshot(ctx.research.snapshot(),source_root=ctx.research.root))
    local = None
    receipt = None
    try:
        prepared=branch.get('design') or {}
        if prepared.get('experience_refs'):
            if prepared.get('experience_files',ctx.research.file_inputs())!=ctx.research.file_inputs():
                raise ResearchConflict('确认前后数据改变，请重新设计')
            ctx.research.bind_experiences(order,prepared['experience_refs'])
        frozen=ctx.research.snapshot()['executions'][order.attempt_id]['experience_basis']
        branch={**branch,'research_contract':{**branch['research_contract'],
                'requirements':frozen['requirements'],'checks':frozen['protocol']}}
        local = worker_context(ctx, order)
        solved = await solve(branch, SimpleNamespace(context=local))
        report,design = solved['report'],solved['design']
        if task.get('experiment') and design.get('experiment')!=task['experiment']:
            raise ResearchConflict('执行报告中的候选方法或评价条件与冻结任务不符')
        checks = [] if report["status"] in {"prepared","comparison_blocked"} or branch["subproblem"].get("kind") == "verify" else report_issues(
            local, report, key, branch["subproblem"].get("depends_on") or [],
            figure_required=needs_figure(branch["subproblem"]))
        if report["status"] not in {"completed", "prepared", "comparison_blocked"}:
            checks.append(report.get("error") or "求解未完成")
        results = [asdict(r) for r in local.registry.for_subproblem(key)]
        reviews = report.get("reviews", [])
        evidence = {item["id"]: item for review in reviews for item in review["evidence"]}
        evidence.update({item["id"]: item for item in results})
        for item in evidence.values():
            if item["path"] and item["id"] not in ctx.research.snapshot()["artifacts"]:
                src = local.workspace / item["path"]
                rel = f"outputs/research/{order.attempt_id}/{item['id']}_{Path(item['path']).name}"
                target = ctx.workspace / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)
                ctx.fs.register_file(rel, kind="image", source_run=item["run_id"], visibility="internal")
                item["path"] = rel
        for review in reviews:
            review["evidence"] = [evidence[item["id"]] for item in review["evidence"]]
        # Immutable run IDs preserve existing grounding and evidence links.
        _copy_tree(local.workspace / "runs", ctx.workspace / "runs")
        for run_id, info in local.fs._manifest.get("runs", {}).items():
            ctx.fs._manifest.setdefault("runs", {})[run_id] = info
        ctx.fs._save_manifest()
        result = WorkerResult(order=order,
            execution_status="succeeded" if report["status"] in {"completed", "prepared"} else "failed",
            acceptance="inconclusive" if report['status']=='comparison_blocked' else "rejected" if checks else "accepted", report=report,
            design=design,
            results=results, claims=[asdict(c) for c in local.registry.claims.values() if c.subproblem == key], issues=[IssueReport(task_id=key, category="evidence", message=message, evidence_refs=[r["id"] for r in results]) for message in checks]
                + [IssueReport.model_validate(i) for i in report.get("research_issues", [])],
            reviews=report.get("reviews", []),
            assumptions=[{**a, "source": source} for source, drafts in (
                ("design", design['chosen'].get("assumptions", [])),
                ("solve", report.get("assumptions", []))) for a in drafts])
        receipt = ctx.research.commit(result)
        refresh_registry(ctx)
        snapshot = ctx.research.snapshot()
        committed = snapshot["experiments"][key]
        await ctx.events.emit("research_state", public_snapshot(snapshot,source_root=ctx.research.root))
        accepted = (receipt["published"] and committed["execution_id"] == order.attempt_id and committed["acceptance"] == "accepted"
                    and committed["validity"] == "current")
        if accepted:
            for item in results:
                if item["path"]:
                    ctx.fs.register_file(item["path"], kind="image", source_run=item["run_id"], visibility="user")
                    await ctx.events.file_changed(item["path"])
            code = local.workspace / "code" / f"{key}_solution.py"
            if code.exists():
                ctx.fs.write_text(f"code/{key}_solution.py", code.read_text(encoding="utf-8"))
        clear = accepted and not any(i["task_id"] == key and i["blocking"] and i["status"] not in {"resolved", "dismissed", "superseded"}
                                     for i in research_findings(snapshot))
        if clear:
            await ctx.events.emit("paper_solved", f"{key} 已通过验收，登记 {len(results)} 项结果")
        else:
            await ctx.events.stage(f"{key} 已提交，{receipt.get('reason') or '仍需验收或修复，请查看研究问题'}")
        return {"log": [f"commit {key}: {receipt}"]}
    except asyncio.CancelledError:
        raise  # Recovery records cancellation and acquires a new lease.
    except Exception as exc:
        if receipt is not None:
            await ctx.events.stage(f"{key} 已提交，结果视图更新失败，将在恢复时重建：{exc}")
            return {"log": [f"projection {key} failed after commit: {exc}"]}
        ctx.research.commit(WorkerResult(order=order, execution_status="failed", acceptance="inconclusive",
            report={"status": "failed", "error": str(exc)}, issues=[IssueReport(task_id=key, category="execution", message=str(exc))]))
        await ctx.events.emit("research_state", public_snapshot(ctx.research.snapshot(),source_root=ctx.research.root))
        await ctx.events.stage(f"{key} 执行失败：{exc}")
        return {"log": [f"attempt {key} failed: {exc}"]}
    finally:
        if local:
            await local.kernels.close()
