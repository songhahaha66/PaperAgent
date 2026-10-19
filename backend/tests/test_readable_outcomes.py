"""Available evidence is independent of acceptance and model review health."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.research.models import WorkerResult, ReviewRecord, IssueReport, ResearchConflict, PlanPatch, TaskChange
from ai_system.research.validation import report_fingerprint
from services.chat_services.research_session import research_snapshot
from services.chat_services import paper_editor
from test_research_control import make_store


def test_result_explanation_is_optional_not_a_semantic_or_provenance_gate():
    from ai_system.research.outcomes import result_view
    item={'id':'R1','name':'预测值','kind':'number','value':'1234.57','unit':'万千瓦时','run_id':'run_exact'}
    view=result_view(item,lambda *args:None)
    assert view['name']=='预测值' and view['unit']=='万千瓦时'
    assert view['run_id']=='run_exact' and view['description']==''
    assert 'meaning_available' not in view


def submit(store, *, fault=False, reviewed=True):
    order = store.claim('Q1', review_required=reviewed)
    ref = store.allocate_ref('R')
    report = {'status': 'completed', 'requirement_evidence': [{'requirement_id': 'RQ1', 'evidence_refs': [ref]}]}
    result = WorkerResult(order=order, execution_status='succeeded', acceptance='accepted', report=report,
                          results=[{'id': ref, 'subproblem': 'Q1', 'kind': 'number', 'name': 'value', 'value': '42', 'run_id': 'run_test'}])
    reviews = [ReviewRecord(report_hash=report_fingerprint(report), issues=[IssueReport(task_id='Q1', category='service', message='review unavailable')] if fault else [])] if reviewed else []
    return store.commit(result.model_copy(update={'reviews': reviews}))


def snapshot(store):
    (store.root / '.system/graph_thread.json').write_text(json.dumps({'thread_id': store.research_id, 'engine_version': 'research-v3'}))
    return research_snapshot(store.root, 'test-outcomes', 'paused')


def test_review_failure_retains_readable_results_without_passing_or_publishing(tmp_path):
    store = make_store(tmp_path)
    submit(store, fault=True)
    before = store.snapshot()
    view = snapshot(store)
    task = next(o for o in view.outcomes if o['task_id'] == 'Q1')
    assert task['computation'] == 'succeeded'
    assert task['availability'] == 'current' and task['results']
    assert task['checking'] == 'service_failed'
    assert task['issues'] and not view.delivery['ready']
    assert store.snapshot() == before, 'projection must not change evidence or issue gates'
    with pytest.raises(ResearchConflict):
        store.publish_paper('unchecked')


def test_no_review_never_becomes_passed_and_missing_decision_stays_missing(tmp_path):
    store = make_store(tmp_path)
    submit(store, reviewed=False)
    task = snapshot(store).outcomes[0]
    assert task['checking'] == 'unreviewed'
    assert task['next_step'] is None


def test_fresh_review_and_versioned_history_are_distinct(tmp_path):
    store = make_store(tmp_path)
    submit(store)
    view = snapshot(store)
    assert view.outcomes[0]['checking'] == 'passed'
    store.apply_patch(PlanPatch(command_id='change', base_revision=1, reason='different input', changes=[TaskChange(task_id='Q1', objective='new scope')]))
    old = snapshot(store).outcomes[0]
    assert old['availability'] == 'historical' and old['checking'] != 'passed'
    assert old['results'], 'stale evidence stays inspectable without entering the active registry'
    assert not snapshot(store).results


def test_legacy_metadata_and_series_are_not_invented(tmp_path):
    (tmp_path / '.system').mkdir()
    (tmp_path / '.system/results.json').write_text(json.dumps({'results': [
        {'id': 'R8', 'subproblem': 'other', 'kind': 'series', 'value': '[2, 4, 8]', 'run_id': 'run_old'},
        {'id': 'R9', 'subproblem': 'other', 'kind': 'text', 'value': 'observed', 'run_id': ''}], 'claims': []}))
    from services.migrate_data import migrate_project
    migrate_project(tmp_path)
    view = research_snapshot(tmp_path, 'legacy', 'paused')
    assert not view.outcomes and not view.results
    saved = next(r for r in view.legacy_records if r['source']=='.system/results.json')
    raw = json.loads(saved['raw'])
    assert raw['results'][0]['value']=='[2, 4, 8]' and 'name' not in raw['results'][0]


def test_empty_paper_does_not_require_checkpoint_or_create_sections(tmp_path):
    store = make_store(tmp_path)
    assert asyncio.run(paper_editor.document(store))['sections'] == []
    assert not (tmp_path / '.system/graph.sqlite').exists()


def test_changed_sources_are_detected_without_mutating_research(tmp_path):
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/source.csv').write_text('value\n1\n')
    store = make_store(tmp_path)
    submit(store)
    assert snapshot(store).outcomes[0]['checking'] == 'passed'
    (tmp_path / 'data/source.csv').write_text('value\n2\n')
    before = store.snapshot()
    view = snapshot(store).outcomes[0]
    assert view['availability'] == 'historical' and view['checking'] == 'outdated'
    assert view['sources'][0]['hash'] == before['attempts'][view['attempt_id']]['input_refs']['data/source.csv']
    assert store.snapshot() == before


def test_deterministic_failure_keeps_evidence_and_blocks_affected_dependency(tmp_path):
    store = make_store(tmp_path, limit=10)
    submit(store)
    view = snapshot(store).outcomes[0]
    assert view['checking'] == 'failed' and view['results']
    assert view['availability'] == 'historical'
    with pytest.raises(ResearchConflict):
        store.claim('Q2')
    # An unrelated task can still execute under the original scheduler.
    store.claim('Q3')


@pytest.mark.parametrize('content', ['', ' \n '])
def test_empty_artifact_does_not_enable_paper_actions(tmp_path, content):
    (tmp_path / 'paper.md').write_text(content)
    view = research_snapshot(tmp_path, 'empty', 'created')
    assert not view.artifacts and not view.has_research


def test_model_suspicion_is_available_as_a_finding_without_invented_failure(tmp_path):
    store = make_store(tmp_path)
    submit(store)
    [issue] = store.report_issues([IssueReport(task_id='Q1', category='model', message='verify scope')], 'sections')
    view = snapshot(store).outcomes[0]
    assert view['availability'] == 'current' and view['computation'] == 'succeeded'
    assert view['checking'] == 'findings' and view['issues'][0]['status'] == 'suspected'
    assert view['issues'][0]['id'] == issue['id']
    store.claim('Q2')  # Existing accepted computations still feed their dependencies.
