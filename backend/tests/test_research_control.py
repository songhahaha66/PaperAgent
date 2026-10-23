"""Perturbation tests for the authoritative research state and publication rules."""

from helpers.research import make_store, finish

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.research.models import ResearchConflict
from ai_system.research.store import ResearchStore
from ai_system.research.planner import ready_tasks


def test_repeat_commit_is_idempotent_and_mismatched_retry_rejected(tmp_path):
    store = make_store(tmp_path)
    result, receipt = finish(store, "Q1")
    seq = store.snapshot()["commit_seq"]
    assert store.commit(result) == receipt
    assert store.snapshot()["commit_seq"] == seq
    with pytest.raises(ResearchConflict):
        store.commit(result.model_copy(update={"report": {"different": True}}))
    assert len(store.registry_payload()["results"]) == 1


def test_rejected_execution_never_unblocks_dependents(tmp_path):
    store = make_store(tmp_path)
    finish(store, "Q1", acceptance="rejected")
    snapshot = store.snapshot()
    assert snapshot["experiments"]["Q1"]["execution_status"] == "succeeded"
    assert snapshot["experiments"]["Q1"]["acceptance"] == "rejected"
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
    result,receipt=finish(store, "Q1", order=order)
    assert not receipt['published']
    state=store.snapshot();task=state['experiments']['Q1'];attempt=state['executions'][order.attempt_id]
    assert task['execution_status']==attempt['execution_status']=='succeeded'
    assert task['validity']==attempt['validity']=='stale' and task['revision']==2
    assert attempt['result']['results']==result.results
    assert not store.registry_payload()['results'] and not store.delivery_ready(state)
    with pytest.raises(ResearchConflict,match='显式重试'):store.claim('Q1')


def test_recovery_uses_commits_and_reclaims_interrupted_attempt(tmp_path):
    store = make_store(tmp_path)
    finish(store, "Q1")
    abandoned = store.claim("Q2")
    before = store.registry_payload()
    recovered = ResearchStore(tmp_path, "research")
    recovered.recover()
    assert recovered.registry_payload() == before
    from ai_system.research.operations import continue_experiment
    from types import SimpleNamespace
    continue_experiment(SimpleNamespace(research=recovered),{'experiment_id':'Q2','information_need':'显式恢复中断的执行，不重跑已接受的上游结果'})
    replacement = recovered.claim("Q2")
    assert replacement.attempt_id != abandoned.attempt_id
    assert replacement.revision == abandoned.revision
    replacement_task=recovered.snapshot()['experiments']['Q2']
    late,receipt=finish(recovered, "Q2", order=abandoned)
    assert not receipt['published']
    assert recovered.snapshot()['experiments']['Q2']==replacement_task
    assert replacement_task['execution_status']=='running' and replacement_task['execution_id']==replacement.attempt_id
    assert finish(recovered, "Q2", order=replacement)[1]["published"]
    completed=recovered.snapshot()
    assert recovered.commit(late)==receipt
    assert recovered.snapshot()==completed


def test_retry_budget_cannot_be_bypassed_by_recovery(tmp_path):
    store = make_store(tmp_path)
    from research_tools_helpers import continue_experiment
    for index in range(3):
        if index:continue_experiment(store,"Q1")
        finish(store, "Q1", acceptance="rejected")
        store.recover()
    assert "Q1" not in ready_tasks(store.snapshot())
    before = store.snapshot()
    with pytest.raises(ResearchConflict, match="预算"):
        continue_experiment(store,"Q1")
    assert store.snapshot() == before


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
