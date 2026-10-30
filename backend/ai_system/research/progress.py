"""Read-only progress derived from the same committed state as scheduling."""

from ai_system.research.domain import findings as research_findings


def issue_next_step(issue, snapshot):
    if issue['category']=='writing':return '按已有证据修订解释或文稿，由当前研究者判断是否需要核查。'
    if issue['category']=='service':return '检查服务未完成，已有计算保留；研究层按信息需求决定是否再次调用。'
    if issue['action']=='recompute' or issue['status']=='repairing':return '系统修复受影响的研究步骤，重新验收后更新文稿。'
    verification=next((t for t in snapshot['experiments'].values() if not t['retired'] and t.get('verifies_issue')==issue['id']),None)
    if verification:return '系统执行已安排的独立核查，根据核查结果继续研究。'
    if issue['source'] in {'sections','review-task:sections'}:
        return '先重新核对这条意见：文字解释错误直接改稿，计算结果有疑点才安排实验核查。'
    return '控制器根据这条意见安排针对性的核查或补充证据。'


def task_progress(snapshot, task, ready, work_status=None):
    key = task["task_id"]
    waiting = [dep for dep in task["dependencies"] if snapshot["experiments"][dep]["acceptance"] != "accepted"
               or snapshot["experiments"][dep]["validity"] != "current"]
    issues = [i for i in research_findings(snapshot) if i["blocking"] and i["status"] not in {"resolved", "dismissed", "superseded"}
              and (i["task_id"] == key or key in i["repair_targets"])
              and (i["action"] != "rewrite")]
    attempts = sum(a["task_id"] == key and a["revision"] == task["revision"] for a in snapshot["executions"].values())
    rework = task["validity"] == "stale" and any(a["task_id"] == key for a in snapshot["executions"].values())
    if task["execution_status"] == "running":
        state = "interrupted" if work_status in {"paused", "cancelled", "failed"} else "running"
    elif task["acceptance"] == "accepted" and task["validity"] == "current" and not issues:
        state = "accepted"
    elif any(i["status"] == "confirmed" and i["action"] == "recompute" for i in issues):
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
            "executions": attempts, "attempt_limit": None, "rework": rework}
