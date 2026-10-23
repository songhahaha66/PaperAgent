"""Shared builders extracted from test_research_control."""
from ai_system.research.store import ResearchStore
from ai_system.research.models import WorkerResult, IssueReport


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
            [{"requirement_id": rid, "evidence_refs": [ref]} for rid in store.snapshot()["experiments"][key]["requirement_ids"]]},
        results=[{"id": ref, "subproblem": key, "kind": "number", "name": "value", "value": "42", "run_id": "run_test"}],
        issues=[IssueReport(task_id=key, category="execution", message="temporary execution failure")] if acceptance == "rejected" else [])
    return result, store.commit(result)
