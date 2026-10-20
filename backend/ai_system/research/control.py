"""Adapters between checkpoint execution and committed research state."""

from ai_system.research.domain import findings as research_findings
import asyncio
import shutil
from dataclasses import asdict
from pathlib import Path

from langgraph.types import Send

from ..graph.registry import Claim, Result
from ..graph.context import GraphContext
from .models import WorkerResult, ResearchConflict, IssueReport
from .planner import ready_tasks
from .assumptions import assumption_scope
from .validation import needs_owner_review
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
            saved['materials']={'inputs':store.file_inputs(),'profile':state.get('data_profile','') if not saved['attempts'] else ''}
            store._save(db,saved,'materials_profiled',saved['materials'])
    store.schedule_investigations()
    from .planner import advance_research
    from .loop import flow
    decision_blocked=False if flow(store).get('blocked') else await advance_research(ctx)
    snapshot = store.snapshot()
    tasks = snapshot["tasks"]
    refresh_registry(ctx)
    # Explicit tombstones remove stale entries via the existing parallel reducer.
    solutions = {key: (task["report"] if task["acceptance"] == "accepted" and task["validity"] == "current" else None)
                 for key, task in tasks.items()}
    designs = {key: task["design"] if task["design"] else
               None if task["validity"] == "stale" else (state.get("designs") or {}).get(key)
               for key, task in tasks.items()}
    sections = {key: section["text"] if section["validity"] == "current" else None
                for key, section in snapshot["sections"].items()}
    sections.update({key: None for key in (state.get("sections") or {}) if key not in sections})
    progress = flow(store)
    blocked = decision_blocked or bool(progress.get('blocked'))
    next_outcome = "blocked" if blocked else snapshot["outcome"] if snapshot["outcome"] in {"completed", "needs_attention"} else "running"
    if next_outcome != snapshot["outcome"]:
        store.set_outcome(next_outcome)
    snapshot = store.snapshot()
    await ctx.events.emit("research_state", public_snapshot(snapshot))
    if blocked:
        await ctx.set_stage("solve", "blocked", "下一步研究决策或资料查证尚未完成，原要求与已验收结果已保留。" if decision_blocked else
                            "存在未关闭的研究或审查问题、未验收任务，或尝试预算已耗尽")
    analysis = analysis_view(snapshot, state.get('analysis'))
    from .planner import current_decision
    selected=current_decision(store)
    outline=(selected[1]['proposal'].get('outline') or []) if selected and selected[1]['proposal']['action']=='deliver' else []
    if selected and selected[1]['proposal']['action']=='deliver' and selected[1]['proposal'].get('title'):
        analysis['title']=selected[1]['proposal']['title']
    if 'write' in progress['done'] and state.get('outline'):outline=state['outline']
    return {"analysis": analysis, "solutions": solutions, "designs": designs,"outline":outline,
            'data_profile':snapshot.get('materials',{}).get('profile','') if snapshot.get('materials',{}).get('inputs')==store.file_inputs() else '材料已变化，旧概览不能作为当前证据，请查看当前文件。',
            "sections": sections, "research_blocked": blocked, "research_restart": False,
            "issues": [i for i in snapshot['feedback'] if i['source'] == 'sections'
                       and i['action'] == 'rewrite' and i['status'] not in {'resolved', 'dismissed'}],
            "repair_round": progress['repairs'], "log": [f"observe: commit {snapshot['commit_seq']}"]}


def analysis_view(snapshot, previous=None):
    """Existing workers/writing consume a projection, never a startup plan."""
    source = snapshot['spec']['requirement']
    return {'title':source.splitlines()[0][:80], 'restatement':source, 'overall_analysis':'',
            **(previous or {}),
            'subproblems':[t['subproblem'] for t in snapshot['tasks'].values() if t['kind']!='verify' and not t['retired']],
            'requirements':snapshot['spec']['requirements'],
            'assumptions':[a for a in snapshot['assumptions'].values() if a['status']!='retired']}


def task_dispatch(state, runtime, task_ids):
    snapshot = runtime.context.research.snapshot()
    if state.get("research_blocked"):
        return []
    ready = ready_tasks(snapshot)
    ready=[key for key in ready if key in task_ids]
    if not ready:
        return []
    sends = []
    from .validation import protocol
    from .planner import decision_records
    replies=[{'ref':ref,'question':row['proposal']['question'],'response':row['response'],
              'requirement_ids':row['proposal']['requirement_ids']} for ref,row in decision_records(runtime.context.research)
             if row.get('status')=='replied' and row.get('proposal',{}).get('action')=='ask']
    for key in ready:
        task = snapshot["tasks"][key]
        sends.append(Send("solve", {"source": snapshot["spec"]["requirement"], "subproblem": task["subproblem"], "analysis": analysis_view(snapshot, state.get("analysis")),
            'task_revision':task['revision'],
            "data_profile": state.get("data_profile", ""),
            'feedback':task.get('method_instruction',''),
            'clarifications':[r for r in replies if set(r['requirement_ids']) & set(task['requirement_ids'])],
            "research_contract": {"requirements": [r for r in snapshot["spec"]["requirements"] if r["id"] in task["requirement_ids"]],
                "task_ids": list(snapshot["tasks"]), "assumption_task_ids": assumption_scope(snapshot["tasks"], key),
                "assumptions": [snapshot["assumptions"][aid] for aid in task["assumption_ids"]], "checks": protocol(snapshot, key)},
            "design": (state.get("designs") or {}).get(key),
            "review_findings": [i for i in research_findings(snapshot) if needs_owner_review(i) and i["task_id"] == key],
            "dependencies": {dep: snapshot["tasks"][dep]["report"] for dep in task["dependencies"]}}))
    return sends


def public_snapshot(snapshot, *, work_status=None):
    ready = set(ready_tasks(snapshot))
    return {key: snapshot[key] for key in ("engine_version", "schema_version", "research_id", "revision", "commit_seq", "outcome")} | {
        "delivery": delivery_status(snapshot),
        "delivery_stages": {key: {**stage, "status": "interrupted" if stage["status"] == "in_progress" and
            work_status in {"paused", "cancelled", "failed"} else stage["status"]}
            for key, stage in snapshot.get("delivery_stages", {}).items()},
        "tasks": [public_task(snapshot, task, ready, work_status) for task in snapshot["tasks"].values() if not task["retired"]],
        "feedback": [i for i in snapshot['feedback'] if i['status'] not in {'resolved', 'dismissed'}],
        "faults": [i for i in snapshot['faults'] if i['status'] not in {'resolved', 'dismissed', 'superseded'}],
        "requirements": [{**r, "status": "accepted" if r["task_ids"] and snapshot["outcome"]=="completed" and all(snapshot["tasks"][key]["acceptance"] == "accepted" and
            snapshot["tasks"][key]["validity"] == "current" and (snapshot["tasks"][key]["kind"] != "writing" or
            snapshot["sections"].get(f"solve:{key}", {}).get("validity") == "current") for key in r["task_ids"]) else "pending"}
            for r in snapshot["spec"]["requirements"]], "assumptions": list(snapshot["assumptions"].values()),
    }


def public_task(snapshot, task, ready=None, work_status=None):
    result = {key: task.get(key) for key in ("task_id", "revision", "objective", "dependencies",
              "execution_status", "acceptance", "validity", "reason", "kind")}
    result["validation"] = current_validation(snapshot, task)
    result["progress"] = task_progress(snapshot, task, set(ready_tasks(snapshot)) if ready is None else ready, work_status)
    return result


def current_validation(snapshot, task):
    # Rejected work is invalidated immediately; retain its verdict for this
    # revision, but never label an older revision's checks as current.
    attempt = next((a for a in reversed(snapshot["attempts"].values())
                    if a["task_id"] == task["task_id"] and a["revision"] == task["revision"]), {})
    return attempt.get("validation")


def delivery_status(snapshot):
    """Explain the existing delivery gate; this projection never changes scheduling."""
    from .store import ResearchStore
    issues = [i for i in research_findings(snapshot) if i["blocking"] and i["status"] not in {"resolved", "dismissed", "superseded"}]
    blockers = [{"task_id": i["task_id"], "message": i["message"], "action": i["action"]} for i in issues]
    covered = {i["task_id"] for i in issues}
    for key, task in snapshot["tasks"].items():
        if task["retired"] or (task["acceptance"] == "accepted" and task["validity"] == "current"):
            continue
        checks = [c["message"] for c in (current_validation(snapshot, task) or {}).get("checks", []) if c["outcome"] != "passed"]
        message = "; ".join(checks) or task.get("reason") or (task.get("report") or {}).get("error")
        action = "retry" if task["execution_status"] in {"failed", "cancelled"} else "wait"
        if task["validity"] == "stale":
            action = "recompute"
        exhausted = len(snapshot["attempts"]) >= snapshot["max_total_attempts"] or sum(
            a["task_id"] == key and a["revision"] == task["revision"] for a in snapshot["attempts"].values()) >= snapshot["max_attempts"]
        if key in covered and not exhausted:
            continue
        if exhausted and task["execution_status"] != "running":
            message = (message + "；" if message else "") + "当前尝试预算已耗尽，需要调整研究计划"
            action = "recompute"
        blockers.append({"task_id": key, "message": message or ("任务正在执行" if task["execution_status"] == "running" else "任务等待执行或依赖验收"), "action": action})
    return {"ready": ResearchStore.delivery_ready(snapshot), "blockers": blockers}


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
        task = snapshot["tasks"][key]
        for dep in task["dependencies"]:
            copy_dependency(dep)
        copied.add(key)
        source = ctx.workspace / ".system" / "attempts" / task["active_attempt"] / "workspace"
        _copy_tree(source / "outputs" / "intermediate", work / "outputs" / "intermediate")
        _copy_tree(source / "code", work / "code")

    for dep in snapshot["tasks"][order.task_id]["dependencies"]:
        copy_dependency(dep)
    local = GraphContext.create(str(work), planner_llm=ctx.planner_llm, coder_llm=ctx.coder_llm,
        writer_llm=ctx.writer_llm, judge=ctx.judge, stream_manager=ctx.events.stream_manager,
        n_candidates=ctx.n_candidates, max_solver_steps=ctx.max_solver_steps,
        max_review_rounds=ctx.max_review_rounds, explore_top_k=ctx.explore_top_k,
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
        task=ctx.research.snapshot()['tasks'][key]
        if branch.get('task_revision',task['revision'])!=task['revision'] or branch['subproblem']!=task['subproblem']:
            raise ResearchConflict('行动所依据的研究版本已改变，请重新观察')
        order = ctx.research.claim(key, review_required=ctx.max_review_rounds > 0 and branch["subproblem"].get("kind") != "writing")
    except ResearchConflict as exc:
        await ctx.events.stage(str(exc))
        return {}
    await ctx.events.emit("research_state", public_snapshot(ctx.research.snapshot()))
    local = None
    receipt = None
    try:
        prepared=branch.get('design') or {}
        if prepared.get('experience_refs'):
            if prepared.get('experience_files',ctx.research.file_inputs())!=ctx.research.file_inputs():
                raise ResearchConflict('确认前后数据改变，请重新设计')
            ctx.research.bind_experiences(order,prepared['experience_refs'])
        local = worker_context(ctx, order)
        update = await solve(branch, SimpleNamespace(context=local))
        report = update["solutions"][key]
        checks = [] if report["status"] in {"prepared","comparison_blocked"} or branch["subproblem"].get("kind") in {"writing", "verify"} else report_issues(
            local, report, key, branch["subproblem"].get("depends_on") or [],
            figure_required=needs_figure(branch["subproblem"]))
        if report["status"] not in {"completed", "prepared", "comparison_blocked"}:
            checks.append(report.get("error") or "求解未完成")
        results = [asdict(r) for r in local.registry.for_subproblem(key)]
        reviews = report.get("reviews", [])
        evidence = {item["id"]: item for review in reviews for item in review["evidence"]}
        evidence.update({item["id"]: item for item in results})
        for item in evidence.values():
            if item["path"]:
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
            design=(update.get("designs") or {}).get(key) or branch.get("design"),
            results=results, claims=[asdict(c) for c in local.registry.claims.values() if c.subproblem == key], issues=[IssueReport(task_id=key, category="evidence", message=message, evidence_refs=[r["id"] for r in results]) for message in checks]
                + [IssueReport.model_validate(i) for i in report.get("research_issues", [])],
            reviews=report.get("reviews", []),
            assumptions=[{**a, "source": source} for source, drafts in (
                ("design", ((update.get("designs") or {}).get(key) or branch.get("design") or {}).get("chosen", {}).get("assumptions", [])),
                ("solve", report.get("assumptions", []))) for a in drafts])
        receipt = ctx.research.commit(result)
        refresh_registry(ctx)
        snapshot = ctx.research.snapshot()
        committed = snapshot["tasks"][key]
        await ctx.events.emit("research_state", public_snapshot(snapshot))
        accepted = (receipt["published"] and committed["acceptance"] == "accepted"
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
        return {"log": update.get("log", []) + [f"commit {key}: {receipt}"]}
    except asyncio.CancelledError:
        raise  # Recovery records cancellation and acquires a new lease.
    except Exception as exc:
        if receipt is not None:
            await ctx.events.stage(f"{key} 已提交，结果视图更新失败，将在恢复时重建：{exc}")
            return {"log": [f"projection {key} failed after commit: {exc}"]}
        ctx.research.commit(WorkerResult(order=order, execution_status="failed", acceptance="inconclusive",
            report={"status": "failed", "error": str(exc)}, issues=[IssueReport(task_id=key, category="execution", message=str(exc))]))
        await ctx.events.emit("research_state", public_snapshot(ctx.research.snapshot()))
        await ctx.events.stage(f"{key} 执行失败：{exc}")
        return {"log": [f"attempt {key} failed: {exc}"]}
    finally:
        if local:
            await local.kernels.close()
