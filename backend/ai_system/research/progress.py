"""Read-only progress derived from the same committed state as scheduling."""

from ai_system.research.domain import findings as research_findings


def task_progress(snapshot, task, ready, work_status=None):
    key = task["task_id"]
    waiting = [dep for dep in task["dependencies"] if snapshot["tasks"][dep]["acceptance"] != "accepted"
               or snapshot["tasks"][dep]["validity"] != "current"]
    issues = [i for i in research_findings(snapshot) if i["blocking"] and i["status"] not in {"resolved", "dismissed", "superseded"}
              and (i["task_id"] == key or key in i["repair_targets"])
              and (i["action"] != "rewrite" or task["kind"] == "writing" and i["section"] == f"solve:{key}")]
    attempts = sum(a["task_id"] == key and a["revision"] == task["revision"] for a in snapshot["attempts"].values())
    rework = task["validity"] == "stale" and any(a["task_id"] == key for a in snapshot["attempts"].values())
    exhausted = attempts >= snapshot["max_attempts"] or len(snapshot["attempts"]) >= snapshot["max_total_attempts"]
    if task["acceptance"] == "accepted" and task["validity"] == "current" and not issues:
        section = snapshot["sections"].get(f"solve:{key}")
        state = "writing" if task["kind"] == "writing" and (not section or section["validity"] != "current") else "accepted"
    elif task["execution_status"] == "running":
        state = "interrupted" if work_status in {"paused", "cancelled", "failed"} else "running"
    elif exhausted or any(i["status"] == "confirmed" and i["action"] == "recompute" for i in issues):
        state = "needs_plan"
    elif task["execution_status"] in {"failed", "cancelled"}:
        state = "failed"
    elif waiting:
        state = "waiting"
    elif any(snapshot["assumptions"][aid]["status"] == "refuted" for aid in task["assumption_ids"]):
        state = "blocked"
    elif key in ready:
        state = "rework" if rework else "ready"
    elif issues or task["acceptance"] in {"rejected", "inconclusive"}:
        state = "review"
    else:
        state = "pending"
    return {"state": state, "waiting_for": waiting, "issue_ids": [i["id"] for i in issues],
            "attempts": attempts, "attempt_limit": snapshot["max_attempts"], "rework": rework}
