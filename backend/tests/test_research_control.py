"""Perturbation tests for the authoritative research state and publication rules."""

from ai_system.research.domain import findings as research_findings
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.research import ENGINE_VERSION
from ai_system.research.models import PlanPatch, TaskChange, WorkerResult, ResearchConflict, IssueReport, Requirement
from ai_system.research.store import ResearchStore
from ai_system.research.planner import ready_tasks


def make_store(tmp_path, limit=None):
    store = ResearchStore(tmp_path, "research")
    quote = "preserve capacity constraints" + (f" <= {limit}" if limit is not None else "")
    store.initialize("Answer every original question; " + quote, {
        "subproblems": [{"id": key, "title": key, "question": key, "depends_on": deps}
                        for key, deps in [("Q1", []), ("Q2", ["Q1"]), ("Q3", [])]],
        "requirements": [{"id": f"RQ{i}", "statement": key, "source_quote": quote, "kind": "goal", "task_ids": [key],
            "checks": [{"id": "capacity", "description": "capacity", "left": {"task_id": "Q1", "name": "value"}, "operator": "le", "expected": limit}] if limit is not None and key == "Q1" else []}
            for i, key in enumerate(("Q1", "Q2", "Q3"), 1)],
        "assumptions": [{"id": "A1", "statement": "stable input", "task_ids": ["Q1", "Q2"], "checks": []}]})
    return store


def finish(store, key, *, acceptance="accepted", order=None):
    order = order or store.claim(key)
    ref = store.allocate_ref("R")
    result = WorkerResult(order=order, execution_status="succeeded", acceptance=acceptance,
        report={"status": "completed", "summary": key, "requirement_evidence":
            [{"requirement_id": rid, "evidence_refs": [ref]} for rid in store.snapshot()["tasks"][key]["requirement_ids"]]},
        results=[{"id": ref, "subproblem": key, "kind": "number", "name": "value", "value": "42", "run_id": "run_test"}],
        issues=[IssueReport(task_id=key, category="execution", message="temporary execution failure")] if acceptance == "rejected" else [])
    return result, store.commit(result)


def test_repeat_commit_is_idempotent_and_mismatched_retry_rejected(tmp_path):
    store = make_store(tmp_path)
    result, receipt = finish(store, "Q1")
    seq = store.snapshot()["commit_seq"]
    assert store.commit(result) == receipt
    assert store.snapshot()["commit_seq"] == seq
    with pytest.raises(ResearchConflict):
        store.commit(result.model_copy(update={"report": {"different": True}}))
    assert len(store.registry_payload()["results"]) == 1


def test_change_invalidates_descendants_and_sections_but_preserves_unrelated_work(tmp_path):
    store = make_store(tmp_path)
    for key in ("Q1", "Q2", "Q3"):
        finish(store, key)
    original = store.registry_payload()
    store.save_sections({"solve:Q1": "a", "solve:Q2": "b", "solve:Q3": "c", "abstract": "all"}, [])
    patch = PlanPatch(command_id="change", base_revision=1, reason="data correction",
                      changes=[TaskChange(task_id="Q1", objective="corrected input")])
    receipt = store.apply_patch(patch)
    assert receipt["affected"] == ["Q1", "Q2"]
    assert store.apply_patch(patch) == receipt
    snapshot = store.snapshot()
    assert snapshot["spec"]["requirement"].startswith("Answer every original question")
    assert snapshot["tasks"]["Q1"]["revision"] == 2
    assert snapshot["sections"]["solve:Q3"]["validity"] == "current"
    assert snapshot["sections"]["abstract"]["validity"] == "stale"
    assert store.registry_payload()["results"] == [r for r in original["results"] if r["subproblem"] == "Q3"]
    assert ready_tasks(snapshot) == ["Q1"]
    finish(store, "Q1")
    assert ready_tasks(store.snapshot()) == ["Q2"]
    assert len(store.snapshot()["evidence"]) == 4  # superseded evidence remains immutable


def test_unrelated_plan_change_does_not_reject_worker(tmp_path):
    store = make_store(tmp_path)
    order = store.claim("Q1")
    store.apply_patch(PlanPatch(command_id="other", base_revision=1, reason="new requirement",
                               changes=[TaskChange(task_id="Q3", objective="new objective")]))
    assert finish(store, "Q1", order=order)[1]["published"]


def test_late_worker_cannot_replace_current_revision(tmp_path):
    store = make_store(tmp_path)
    old = store.claim("Q1")
    store.apply_patch(PlanPatch(command_id="change", base_revision=1, reason="assumption refuted",
                               changes=[TaskChange(task_id="Q1", objective="use A2")]))
    new, _ = finish(store, "Q1")
    _, receipt = finish(store, "Q1", order=old)
    assert not receipt["published"]
    assert store.snapshot()["tasks"]["Q1"]["active_attempt"] == new.order.attempt_id
    assert len(store.registry_payload()["results"]) == 1


def test_rejected_execution_never_unblocks_dependents(tmp_path):
    store = make_store(tmp_path)
    finish(store, "Q1", acceptance="rejected")
    snapshot = store.snapshot()
    assert snapshot["tasks"]["Q1"]["execution_status"] == "succeeded"
    assert snapshot["tasks"]["Q1"]["acceptance"] == "rejected"
    assert "Q2" not in ready_tasks(snapshot)
    assert store.registry_payload()["results"] == []
    with pytest.raises(ResearchConflict):
        store.claim("Q2")


def test_file_correction_during_execution_rejects_result(tmp_path):
    store = make_store(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "input.csv").write_text("old")
    order = store.claim("Q1")
    (data / "input.csv").write_text("corrected")
    assert not finish(store, "Q1", order=order)[1]["published"]
    assert store.snapshot()["tasks"]["Q1"]["execution_status"] == "pending"
    assert store.snapshot()["tasks"]["Q1"]["revision"] == 2


def test_invalid_or_stale_plan_rolls_back_atomically(tmp_path):
    store = make_store(tmp_path)
    initial = store.snapshot()
    with pytest.raises(ValueError, match="环"):
        store.apply_patch(PlanPatch(command_id="cycle", base_revision=1, reason="cycle",
            changes=[TaskChange(task_id="Q1", objective="x", dependencies=["Q2"])]))
    assert store.snapshot() == initial
    with pytest.raises(ResearchConflict):
        store.apply_patch(PlanPatch(command_id="stale", base_revision=2, reason="stale",
                                   changes=[TaskChange(task_id="Q1", objective="x")]))
    assert store.snapshot() == initial


def test_recovery_uses_commits_and_reclaims_interrupted_attempt(tmp_path):
    store = make_store(tmp_path)
    finish(store, "Q1")
    abandoned = store.claim("Q2")
    before = store.registry_payload()
    recovered = ResearchStore(tmp_path, "research")
    recovered.recover()
    assert recovered.registry_payload() == before
    replacement = recovered.claim("Q2")
    assert replacement.attempt_id != abandoned.attempt_id
    assert replacement.revision == abandoned.revision
    assert not finish(recovered, "Q2", order=abandoned)[1]["published"]
    assert finish(recovered, "Q2", order=replacement)[1]["published"]


def test_retry_budget_cannot_be_bypassed_by_recovery(tmp_path):
    store = make_store(tmp_path)
    for _ in range(3):
        finish(store, "Q1", acceptance="rejected")
        store.recover()
    assert "Q1" not in ready_tasks(store.snapshot())
    with pytest.raises(ResearchConflict, match="预算"):
        store.claim("Q1")


def test_data_correction_after_commit_invalidates_all_coarse_file_consumers(tmp_path):
    store = make_store(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "input.csv").write_text("old")
    finish(store, "Q1")
    finish(store, "Q2")
    (data / "input.csv").write_text("corrected")
    assert store.refresh_inputs()["tasks"]["Q1"]["acceptance"] == "pending"
    assert not store.registry_payload()["results"]


def test_manual_edit_preserves_both_paper_versions(tmp_path):
    store = make_store(tmp_path)
    for key in ("Q1", "Q2", "Q3"):
        finish(store, key)
    assert store.publish_paper("generated one") == ("paper.md", False)
    (tmp_path / "paper.md").write_text("human edits")
    path, conflict = store.publish_paper("generated two")
    assert conflict and (tmp_path / path).read_text() == "generated two"
    assert (tmp_path / "paper.md").read_text() == "human edits"
    assert store.snapshot()["outcome"] == "needs_attention"


def test_planner_can_add_validation_task_without_losing_requirements(tmp_path):
    store = make_store(tmp_path)
    store.apply_patch(PlanPatch(command_id="verification", base_revision=1, reason="need baseline",
        changes=[TaskChange(action="add", task_id="Q4", title="baseline", objective="validate baseline", requirement_ids=["RQ1"]),
                 TaskChange(task_id="Q1", objective="compare against Q4", dependencies=["Q4"])]))
    assert set(store.snapshot()["tasks"]) == {"Q1", "Q2", "Q3", "Q4"}
    assert "Q4" in ready_tasks(store.snapshot()) and "Q1" not in ready_tasks(store.snapshot())


def test_snapshot_is_read_only_and_uses_authoritative_database(tmp_path):
    from services.chat_services.research_session import research_snapshot
    store = make_store(tmp_path)
    finish(store, "Q1")
    thread = tmp_path / ".system" / "graph_thread.json"
    thread.write_text(json.dumps({"engine_version": ENGINE_VERSION, "thread_id": "research", "status": "blocked"}))
    (thread.parent / "results.json").write_text('{broken projection')
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    snapshot = research_snapshot(tmp_path, "work", "paused")
    assert len(snapshot.results) == 1 and snapshot.tasks
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_single_claim_wins_when_schedulers_race(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = make_store(tmp_path)

    def claim():
        try:
            return store.claim("Q1")
        except ResearchConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        orders = list(pool.map(lambda _: claim(), range(2)))
    assert sum(order is not None for order in orders) == 1


def test_duplicate_revision_command_reuses_original_objective(tmp_path):
    from ai_system.research.planner import propose_revision
    store = make_store(tmp_path)
    command = dict(command_id="human", base_revision=1, change="use corrected assumption", scope=["Q1"])
    requirements = [Requirement(id="RQ4", statement=command["change"], source_quote=command["change"], kind="goal", task_ids=["Q1"])]
    patch = propose_revision(store.snapshot(), **command, requirements=requirements)
    first = store.apply_revision_command(**command, patch=patch)
    retried = propose_revision(store.snapshot(), **command, requirements=requirements)
    assert store.apply_revision_command(**command, patch=retried) == first
    assert store.snapshot()["tasks"]["Q1"]["objective"].count("补充要求") == 1


def test_added_task_invalidates_existing_global_summaries(tmp_path):
    store = make_store(tmp_path)
    store.save_sections({"abstract": "original three questions", "solve:Q3": "unrelated"}, [])
    store.apply_patch(PlanPatch(command_id="add", base_revision=1, reason="new verification",
        changes=[TaskChange(action="add", task_id="Q4", objective="new verification", requirement_ids=["RQ1"])]))
    assert store.snapshot()["sections"]["abstract"]["validity"] == "stale"
    assert store.snapshot()["sections"]["solve:Q3"]["validity"] == "current"


def test_observe_uses_confirmed_failure_to_commit_planner_proposal(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from ai_system.research.control import observe
    from ai_system.research.models import PlanProposal
    from ai_system.research.planner import Planner
    from ai_system.graph.context import GraphContext
    store = make_store(tmp_path, limit=40)
    finish(store, "Q1")

    async def propose(self, snapshot):
        return PlanProposal(reason="add capacity check", issue_ids=[i["id"] for i in research_findings(snapshot) if i["status"] == "confirmed"],
                            changes=[TaskChange(task_id="Q1", objective="enforce capacity before publishing")])

    monkeypatch.setattr(Planner, "propose", propose)
    ctx = GraphContext.create(str(tmp_path), planner_llm=object())
    ctx.research = store
    state = {"task": "original", "analysis": {"subproblems": [t["subproblem"] for t in store.snapshot()["tasks"].values()]}}
    result = asyncio.run(observe(state, SimpleNamespace(context=ctx)))
    assert not result["research_blocked"]
    assert store.snapshot()["revision"] == 2
    assert store.snapshot()["planning_calls"] == 1
    assert store.registry_payload()["results"] == []


def test_staged_value_change_gets_new_reference_without_rebinding_old_reference(tmp_path):
    from ai_system.graph.registry import ResultRegistry
    from services.file_services.workspace_fs import WorkspaceFS
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run = fs.create_run()
    fs.save_run_stdout(run, "old 1.25 new 2.75")
    fs.finish_run(run, "success")
    registry = ResultRegistry(fs)
    first = registry.record_value("Q1", "estimate", "1.25", run)
    second = registry.record_value("Q1", "estimate", "2.75", run)
    assert first.id != second.id
    assert registry.get(first.id) is None
    assert registry.staged_history[0].value == "1.25"
    assert registry.for_subproblem("Q1") == [second]


def test_section_history_retains_previous_text_and_context(tmp_path):
    import sqlite3
    store = make_store(tmp_path)
    store.save_sections({"solve:Q1": "original"}, [], {"solve:Q1": "context-one"})
    store.save_sections({"solve:Q1": "revised"}, [], {"solve:Q1": "context-two"})
    with sqlite3.connect(store.path) as db:
        entries = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM journal WHERE kind='sections_committed' ORDER BY seq")]
    assert entries[0]["versions"]["solve:Q1"]["text"] == "original"
    assert entries[0]["versions"]["solve:Q1"]["context_hash"] == "context-one"
    assert entries[1]["versions"]["solve:Q1"]["revision"] == 2
