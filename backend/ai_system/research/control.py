"""Adapters between checkpoint execution and committed research state."""
import asyncio
import shutil
from dataclasses import asdict
from pathlib import Path

from langgraph.graph import END
from langgraph.types import Send

from ..graph.registry import Claim, Result
from ..graph.context import GraphContext
from .models import WorkerResult, ResearchConflict, PlanPatch
from .planner import ready_tasks, Planner


def refresh_registry(ctx):
    payload = ctx.research.registry_payload()
    ctx.registry.results = {r["id"]: Result(**r) for r in payload["results"]}
    ctx.registry.claims = {c["id"]: Claim(**c) for c in payload["claims"]}
    ctx.registry.save()


async def observe(state, runtime):
    ctx = runtime.context
    store = ctx.research
    store.initialize(state["task"], state["analysis"])
    snapshot = store.refresh_inputs()
    refresh_registry(ctx)
    tasks = snapshot["tasks"]
    # Only deterministic validation failures trigger automatic replanning.
    # Execution/service failures require an explicit resume and fresh lease.
    rejected = {key for key, t in tasks.items() if t["execution_status"] == "succeeded" and t["acceptance"] == "rejected"}
    if rejected and snapshot["planning_calls"] < ctx.max_planning_calls and store.reserve_planning_call():
        try:
            proposal = await Planner(ctx.planner_llm).propose(snapshot)
            if proposal.changes and set(proposal.evidence_refs) & rejected and store.record_proposal(proposal.model_dump()):
                changes = [change.model_copy(update={"objective": tasks[change.task_id]["objective"] +
                           "\n\n补充研究策略：" + change.objective}) if change.action == "revise" and change.task_id in tasks
                           else change for change in proposal.changes]
                store.apply_patch(PlanPatch(command_id=f"planner:{snapshot['commit_seq']}",
                    base_revision=snapshot["revision"], reason=proposal.reason or "补充验证", changes=changes))
                snapshot = store.snapshot()
                tasks = snapshot["tasks"]
        except Exception as exc:
            await ctx.events.stage(f"规划未提交，保留已验收证据：{exc}")
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
    complete = all(t["acceptance"] == "accepted" and t["validity"] == "current" for t in tasks.values())
    blocked = not complete and not ready_tasks(snapshot)
    store.set_outcome("blocked" if blocked else "running")
    snapshot = store.snapshot()
    await ctx.events.emit("research_state", public_snapshot(snapshot))
    if complete:
        await ctx.set_stage("design", "completed")
    if blocked:
        await ctx.set_stage("solve", "blocked", "存在未通过验收的任务或尝试预算已耗尽")
    analysis = {**state["analysis"], "subproblems": [t["subproblem"] for t in tasks.values()]}
    return {"analysis": analysis, "solutions": solutions, "designs": designs,
            "sections": sections, "research_blocked": blocked,
            "issues": [], "repair_round": 0, "log": [f"observe: commit {snapshot['commit_seq']}"]}


def dispatch(state, runtime):
    snapshot = runtime.context.research.snapshot()
    if state.get("research_blocked"):
        return END
    ready = ready_tasks(snapshot)
    if not ready:
        return "reconcile"
    sends = []
    for key in ready:
        task = snapshot["tasks"][key]
        sends.append(Send("solve", {"subproblem": task["subproblem"], "analysis": state["analysis"],
            "data_profile": state.get("data_profile", ""),
            "design": (state.get("designs") or {}).get(key),
            "dependencies": {dep: snapshot["tasks"][dep]["report"] for dep in task["dependencies"]}}))
    return sends


def public_snapshot(snapshot):
    return {key: snapshot[key] for key in ("engine_version", "schema_version", "research_id", "revision", "commit_seq", "outcome")} | {
        "tasks": [{key: task.get(key) for key in ("task_id", "revision", "objective", "dependencies",
                   "execution_status", "acceptance", "validity", "reason")} for task in snapshot["tasks"].values()],
        "issues": snapshot["issues"],
    }


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
        explore_steps=ctx.explore_steps, explore_margin=ctx.explore_margin,
        template_requirements=ctx.template_requirements, check_claims=ctx.check_claims)
    local.registry.results = dict(ctx.registry.results)
    local.registry.claims = dict(ctx.registry.claims)
    local.registry.allocate_id = ctx.research.allocate_ref
    # Progress belongs to the coordinator. Attempts only emit local messages.
    local.set_stage = ctx.set_stage
    return local


async def execute(branch, runtime):
    from ..graph.nodes.solve import solve
    from ..graph.nodes.review import report_issues, needs_figure
    from ..graph.nodes.explore import is_hypothesis_test
    from types import SimpleNamespace

    ctx = runtime.context
    key = branch["subproblem"]["id"]
    try:
        order = ctx.research.claim(key)
    except ResearchConflict as exc:
        await ctx.events.stage(str(exc))
        return {}
    local = None
    receipt = None
    try:
        local = worker_context(ctx, order)
        update = await solve(branch, SimpleNamespace(context=local))
        report = update["solutions"][key]
        checks = [] if branch["subproblem"].get("kind") == "writing" else report_issues(
            local, report, key, branch["subproblem"].get("depends_on") or [],
            hypothesis=is_hypothesis_test(branch["subproblem"]), figure_required=needs_figure(branch["subproblem"]))
        if report["status"] != "completed":
            checks.append(report.get("error") or "求解未完成")
        results = [asdict(r) for r in local.registry.for_subproblem(key)]
        for item in results:
            if item["path"]:
                src = local.workspace / item["path"]
                rel = f"outputs/research/{order.attempt_id}/{Path(item['path']).name}"
                target = ctx.workspace / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)
                ctx.fs.register_file(rel, kind="image", source_run=item["run_id"], visibility="internal")
                item["path"] = rel
        # Immutable run IDs preserve existing grounding and evidence links.
        _copy_tree(local.workspace / "runs", ctx.workspace / "runs")
        for run_id, info in local.fs._manifest.get("runs", {}).items():
            ctx.fs._manifest.setdefault("runs", {})[run_id] = info
        ctx.fs._save_manifest()
        result = WorkerResult(order=order,
            execution_status="succeeded" if report["status"] == "completed" else "failed",
            acceptance="rejected" if checks else "accepted", report=report,
            design=(update.get("designs") or {}).get(key) or branch.get("design"),
            results=results, claims=[asdict(c) for c in local.registry.claims.values() if c.subproblem == key], issues=checks)
        receipt = ctx.research.commit(result)
        refresh_registry(ctx)
        if receipt["published"] and not checks:
            for item in results:
                if item["path"]:
                    ctx.fs.register_file(item["path"], kind="image", source_run=item["run_id"], visibility="user")
            code = local.workspace / "code" / f"{key}_solution.py"
            if code.exists():
                ctx.fs.write_text(f"code/{key}_solution.py", code.read_text(encoding="utf-8"))
        return {"log": update.get("log", []) + [f"commit {key}: {receipt}"]}
    except asyncio.CancelledError:
        raise  # Recovery records cancellation and acquires a new lease.
    except Exception as exc:
        if receipt is not None:
            await ctx.events.stage(f"{key} 已提交，兼容视图更新失败，将在恢复时重建：{exc}")
            return {"log": [f"projection {key} failed after commit: {exc}"]}
        ctx.research.commit(WorkerResult(order=order, execution_status="failed", acceptance="inconclusive",
            report={"status": "failed", "error": str(exc)}, issues=[str(exc)]))
        await ctx.events.stage(f"{key} 执行失败：{exc}")
        return {"log": [f"attempt {key} failed: {exc}"]}
    finally:
        if local:
            await local.kernels.close()
