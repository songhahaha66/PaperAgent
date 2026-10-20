"""Read-only progress derived from the same committed state as scheduling."""

from ai_system.research.domain import findings as research_findings


def issue_next_step(issue, snapshot):
    if issue['category']=='writing':return '系统按现有证据修改对应文稿，再自动复核。'
    if issue['category']=='service':return '恢复后重试未完成的检查，保留已有计算结果。'
    if issue['action']=='recompute' or issue['status']=='repairing':return '系统修复受影响的研究步骤，重新验收后更新文稿。'
    verification=next((t for t in snapshot['tasks'].values() if not t['retired'] and t.get('verifies_issue')==issue['id']),None)
    if verification:return '系统执行已安排的独立核查，根据核查结果继续研究。'
    if issue['source'] in {'sections','review-task:sections'}:
        return '先重新核对这条意见：文字解释错误直接改稿，计算结果有疑点才安排实验核查。'
    return '控制器根据这条意见安排针对性的核查或补充证据。'


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
            "next_step": ' '.join(dict.fromkeys(issue_next_step(i,snapshot) for i in issues)),
            "attempts": attempts, "attempt_limit": snapshot["max_attempts"], "rework": rework}
