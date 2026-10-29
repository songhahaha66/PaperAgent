"""Available evidence is independent of acceptance and model review health."""
from services.file_services.workspace_layout import workspace_path
import json
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.research.models import WorkerResult, ReviewRecord, IssueReport, ResearchConflict
from ai_system.research.validation import report_fingerprint
from services.chat_services.research_session import research_snapshot
from helpers.research import make_store


def submit(store, *, fault=False, reviewed=True):
    order = store.claim('Q1')
    ref = store.allocate_ref('R')
    report = {'status': 'completed', 'requirement_evidence': [{'requirement_id': 'RQ1', 'evidence_refs': [ref]}]}
    result = WorkerResult(order=order, execution_status='succeeded', acceptance='accepted', report=report,
                          results=[{'id': ref, 'subproblem': 'Q1', 'kind': 'number', 'name': 'value', 'value': '42', 'run_id': 'run_test'}])
    reviews = [ReviewRecord(report_hash=report_fingerprint(report), issues=[IssueReport(task_id='Q1', category='service', message='review unavailable')] if fault else [])] if reviewed else []
    return store.commit(result.model_copy(update={'reviews': reviews}))


def snapshot(store):
    (workspace_path(store.root, '.system/graph_thread.json')).write_text(json.dumps({'thread_id': store.research_id, 'engine_version': 'research-v3'}))
    return research_snapshot(store.root, 'test-outcomes', 'paused')


def test_advisory_review_failure_retains_results_and_unanswered_research_is_not_publishable(tmp_path):
    store = make_store(tmp_path)
    submit(store, fault=True)
    before = store.snapshot()
    view = snapshot(store)
    task = next(o for o in view.experiments if o['task_id'] == 'Q1')
    assert task['computation'] == 'succeeded'
    assert task['availability'] == 'current' and task['results']
    assert task['checking'] == 'service_failed'
    assert task['issues'] and not view.delivery['ready']
    assert store.snapshot() == before, 'projection must not change evidence or issue gates'
    with pytest.raises(ResearchConflict):
        store.publish_paper('unchecked')


def test_deterministic_failure_keeps_evidence_and_blocks_affected_dependency(tmp_path):
    store = make_store(tmp_path, limit=10)
    submit(store)
    view = snapshot(store).experiments[0]
    assert view['checking'] == 'failed' and view['results']
    assert view['availability'] == 'historical'
    assert view['results'][0]['value']=='42' and view['record']['status']=='completed'
    assert not store.registry_payload()['results'] and not store.delivery_ready(store.snapshot())
    with pytest.raises(ResearchConflict):
        store.claim('Q2')
    # An unrelated task can still execute through its explicit execution handoff.
    store.claim('Q3')
