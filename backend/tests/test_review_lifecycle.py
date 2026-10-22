"""Local audit findings and newly adopted premises share committed research state."""

from ai_system.research.domain import findings as research_findings
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.research.models import IssueReport, ResearchConflict, ReviewRecord, WorkerResult
from ai_system.research.validation import report_fingerprint
from helpers.contracts import store_for


@pytest.mark.parametrize("record_kind", ["missing", "stale", "open"])
def test_optional_reviews_do_not_gate_results_or_relax_execution_ownership(tmp_path, record_kind):
    store = store_for(tmp_path)
    order = store.claim("Q1")
    report = {"status": "completed", "summary": "computed", "requirement_evidence": [{"requirement_id": "RQ1", "evidence_refs": ["R1"]}]}
    record = ReviewRecord(report_hash="stale" if record_kind == "stale" else report_fingerprint(report), issues=[] if record_kind != "open" else [IssueReport(task_id="Q1", category="model", message="unresolved")])
    result = WorkerResult(order=order, execution_status="succeeded", acceptance="accepted", report=report,
        results=[{"id": "R1", "subproblem": "Q1", "name": "demand", "value": "8", "run_id": "run_test"}],
        reviews=[] if record_kind == "missing" else [record])
    store.commit(result)
    assert not store.delivery_ready(store.snapshot())
    state = store.snapshot()
    assert state['experiments']['Q1']['acceptance'] == 'accepted'
    assert [r['id'] for r in store.registry_payload()['results']] == ['R1']
    issues = research_findings(state)
    if record_kind == 'open':
        assert any(i['source'] == 'local_review' and i['status'] == 'suspected' for i in issues)
        assert all(not i['blocking'] for i in issues)
    else:
        assert not issues, 'missing or stale optional review cannot manufacture a scientific finding'
    # A real question answer is still required; advisory review is not its replacement.
    with pytest.raises(ResearchConflict, match="冻结输入"):
        store.commit(result.model_copy(update={"order": order.model_copy(update={"attempt_id": store.claim("Q3").attempt_id})}))
