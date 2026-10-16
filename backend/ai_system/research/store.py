"""SQLite is authoritative; checkpoints and results.json are projections.

Each command holds BEGIN IMMEDIATE through validation and publication. Documents
are intentionally coarse in this first schema: old revisions and attempts remain
in the journal, while reads see a single committed, consistent snapshot.
"""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager, closing
from pathlib import Path

from . import ENGINE_VERSION
from .models import PlanPatch, ResearchConflict, WorkerResult, WorkOrder, Issue, IssueReport, ValidationRecord
from .validation import validate_spec, protocol, evaluate, fingerprint

DB_PATH = ".system/research.sqlite"


def digest(value) -> str:
    return fingerprint(value)


def validate_dependencies(tasks: dict) -> None:
    if any(not re.fullmatch(r"Q[0-9]+[a-z]?", key) for key in tasks):
        raise ValueError("任务编号应为 Q1、Q2 或 Q1a 等格式")
    visiting, visited = set(), set()

    def visit(key):
        if key not in tasks:
            raise ValueError(f"未知依赖：{key}")
        if key in visiting:
            raise ValueError(f"任务依赖存在环：{key}")
        if key in visited:
            return
        visiting.add(key)
        for dep in tasks[key]["dependencies"]:
            visit(dep)
        visiting.remove(key)
        visited.add(key)

    for key in tasks:
        visit(key)


class ResearchStore:
    def __init__(self, workspace: str | Path, research_id: str, *, read_only: bool = False):
        self.root = Path(workspace).resolve()
        self.path = self.root / DB_PATH
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", research_id):
            raise ValueError("研究标识格式错误")
        self.research_id = research_id
        self.read_only = read_only
        if not self.path.resolve().is_relative_to(self.root):
            raise ValueError("研究数据库必须位于工作区内")
        if read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS journal (
                    research_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(research_id, seq));
                CREATE TABLE IF NOT EXISTS commands (
                    research_id TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL,
                    response TEXT NOT NULL, PRIMARY KEY(research_id, id));
                CREATE TABLE IF NOT EXISTS counters (research_id TEXT NOT NULL, prefix TEXT NOT NULL, value INTEGER NOT NULL,
                    PRIMARY KEY(research_id, prefix));
            """)

    def _connect(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=30) if self.read_only else sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _transaction(self):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _read(self, db):
        row = db.execute("SELECT payload FROM research WHERE id=?", (self.research_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def snapshot(self):
        with closing(self._connect()) as db:
            return self._read(db)

    def _save(self, db, state, kind, payload):
        state["commit_seq"] += 1
        db.execute("INSERT OR REPLACE INTO research VALUES (?, ?)",
                   (self.research_id, json.dumps(state, ensure_ascii=False)))
        db.execute("INSERT INTO journal VALUES (?, ?, ?, ?)",
                   (self.research_id, state["commit_seq"], kind, json.dumps(payload, ensure_ascii=False)))

    def initialize(self, requirement: str, analysis: dict, max_attempts: int = 3):
        with self._transaction() as db:
            state = self._read(db)
            if state:
                return state
            requirements, assumptions = validate_spec(requirement, analysis)
            tasks = {sp["id"]: {
                "task_id": sp["id"], "revision": 1, "objective": sp["question"],
                "dependencies": sp.get("depends_on") or [], "subproblem": sp,
                "execution_status": "pending", "acceptance": "pending", "validity": "current",
                "active_attempt": None, "report": None, "design": None,
                "requirement_ids": [r.id for r in requirements if sp["id"] in r.task_ids],
                "assumption_ids": [a.id for a in assumptions if sp["id"] in a.task_ids],
                "kind": sp.get("kind", "compute"), "retired": False,
            } for sp in analysis["subproblems"]}
            validate_dependencies(tasks)
            state = {"schema_version": 2, "engine_version": ENGINE_VERSION,
                     "research_id": self.research_id, "revision": 1, "commit_seq": 0,
                     "spec": {"requirement": requirement, "amendments": [],
                              "requirements": [{**r.model_dump(), "revision": 1} for r in requirements]},
                     "assumptions": {a.id: {**a.model_dump(), "revision": 1, "status": "active"} for a in assumptions},
                     "assumption_history": [], "validations": [],
                     "tasks": tasks, "attempts": {}, "evidence": {}, "claims": {},
                     "sections": {}, "issues": [], "outcome": "running", "max_attempts": max_attempts,
                     "planning_calls": 0, "proposal_hashes": [], "max_total_attempts": len(tasks) * max_attempts + 6}
            self._validate_contracts(state)
            self._save(db, state, "initialized", {"spec": state["spec"], "tasks": tasks})
            return state

    def allocate_ref(self, prefix: str):
        with self._transaction() as db:
            db.execute("INSERT INTO counters VALUES (?, ?, 1) ON CONFLICT(research_id, prefix) DO UPDATE SET value=value+1", (self.research_id, prefix))
            value = db.execute("SELECT value FROM counters WHERE research_id=? AND prefix=?", (self.research_id, prefix)).fetchone()[0]
            return f"{prefix}{value}"

    def _artifact_hash(self, item):
        if not item.get("path"):
            return None
        path = (self.root / item["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("产物路径超出工作区")
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def file_inputs(self):
        refs = {}
        # Unknown file-level dependencies are tracked conservatively. Generated
        # handoff files are frozen in the attempt workspace by the worker adapter.
        for name in ("data", "attachment"):
            folder = self.root / name
            for path in sorted(folder.rglob("*")) if folder.exists() else []:
                if path.is_file():
                    if not path.resolve().is_relative_to(self.root):
                        raise ValueError("研究输入不能指向工作区外部")
                    with path.open("rb") as stream:
                        refs[path.relative_to(self.root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        return refs

    @staticmethod
    def _inputs(state, task, files):
        refs = dict(files)
        refs.update({f"task:{dep}": state["tasks"][dep]["active_attempt"] or ""
                     for dep in task["dependencies"]})
        refs.update({f"assumption:{aid}": str(state["assumptions"][aid]["revision"])
                     for aid in task["assumption_ids"]})
        return refs

    def refresh_inputs(self):
        files = self.file_inputs()
        with self._transaction() as db:
            state = self._read(db)
            changed = set()
            for key, task in state["tasks"].items():
                attempt = state["attempts"].get(task["active_attempt"])
                if task["acceptance"] == "accepted" and attempt:
                    previous = {k: v for k, v in attempt["input_refs"].items() if not k.startswith(("task:", "assumption:"))}
                    if previous != files:
                        changed.add(key)
            if changed:
                for key in changed:
                    state["tasks"][key]["revision"] += 1
                affected = self._invalidate(state, changed, "原始数据文件已更正")
                state["revision"] += 1
                self._save(db, state, "inputs_changed", {"affected": affected})
            return state

    def claim(self, task_id: str) -> WorkOrder:
        files = self.file_inputs()
        with self._transaction() as db:
            state = self._read(db)
            task = state["tasks"][task_id]
            if task["retired"] or task["execution_status"] != "pending":
                raise ResearchConflict(f"任务 {task_id} 已领取或需要显式重试")
            if any(state["tasks"][dep]["acceptance"] != "accepted" or
                   state["tasks"][dep]["validity"] != "current" for dep in task["dependencies"]):
                raise ResearchConflict(f"任务 {task_id} 的依赖尚未验收")
            if any(state["assumptions"][aid]["status"] == "refuted" for aid in task["assumption_ids"]):
                raise ResearchConflict("任务依赖的假设已被反证")
            if any(i["blocking"] and i["status"] == "confirmed" and task_id in i["repair_targets"] for i in state["issues"]):
                raise ResearchConflict("任务需要先提交修复计划")
            attempts = [a for a in state["attempts"].values() if a["task_id"] == task_id and a["revision"] == task["revision"]]
            if len(attempts) >= state["max_attempts"] or len(state["attempts"]) >= state["max_total_attempts"]:
                raise ResearchConflict(f"任务 {task_id} 已达到尝试预算")
            refs = self._inputs(state, task, files)
            order = WorkOrder(attempt_id=uuid.uuid4().hex, task_id=task_id, revision=task["revision"],
                              input_digest=digest(refs), input_refs=refs, lease=uuid.uuid4().hex,
                              protocol_hash=digest(protocol(state, task_id)))
            state["attempts"][order.attempt_id] = {**order.model_dump(), "execution_status": "running", "started_at": time.time()}
            task.update(execution_status="running", active_attempt=order.attempt_id)
            self._save(db, state, "claimed", order.model_dump())
            return order

    def commit(self, result: WorkerResult):
        signature = digest(result.model_dump())
        files = self.file_inputs()
        with self._transaction() as db:
            state = self._read(db)
            attempt = state["attempts"][result.order.attempt_id]
            if attempt.get("commit"):
                if attempt["result_digest"] != signature:
                    raise ResearchConflict("同一尝试不能提交不同结果")
                return attempt["commit"]
            order = result.order
            task = state["tasks"][order.task_id]
            if any(attempt[key] != getattr(order, key) for key in ("task_id", "revision", "lease", "input_digest", "input_refs", "protocol_hash")):
                raise ResearchConflict("执行权或冻结输入不匹配")
            current = (task["revision"] == order.revision and task["active_attempt"] == order.attempt_id
                       and attempt["execution_status"] == "running"
                       and self._inputs(state, task, files) == order.input_refs
                       and digest(protocol(state, order.task_id)) == order.protocol_hash)
            receipt = {"attempt_id": order.attempt_id, "published": current, "commit_seq": state["commit_seq"] + 1}
            attempt.update(execution_status=result.execution_status, acceptance=result.acceptance,
                           result_digest=signature, commit=receipt, result=result.model_dump())
            if current:
                if result.acceptance == "accepted" and result.execution_status != "succeeded":
                    raise ValueError("执行失败的结果不能通过验收")
                # Evidence identifiers never point at a different value later.
                for item in result.results:
                    if item["id"] in state["evidence"] or item["subproblem"] != order.task_id:
                        raise ValueError("结果编号重复或不属于当前任务")
                ids = {r["id"] for r in result.results}
                frozen = {v for k, v in order.input_refs.items() if k.startswith("task:")}
                input_evidence = {rid for rid, entry in state["evidence"].items() if entry["producer_attempt"] in frozen}
                for claim in result.claims:
                    if claim["id"] in state["claims"] or claim["subproblem"] != order.task_id:
                        raise ValueError("结论编号重复或不属于当前任务")
                    if not set(claim.get("evidence", [])) <= (ids | input_evidence):
                        raise ValueError("结论引用了不可用证据")
                for item in result.results:
                    state["evidence"][item["id"]] = {"payload": item, "content_hash": digest(item),
                        "artifact_hash": self._artifact_hash(item), "producer_attempt": order.attempt_id}
                for item in result.claims:
                    state["claims"][item["id"]] = {"payload": item, "producer_attempt": order.attempt_id,
                        "assumption_refs": {aid: state["assumptions"][aid]["revision"] for aid in task["assumption_ids"]}}
                validation = evaluate(state, order.task_id, result.results) if result.execution_status == "succeeded" else ValidationRecord(
                    protocol_hash=order.protocol_hash, checks=[], outcome="inconclusive")
                attempt["validation"] = validation.model_dump()
                state["validations"].append({"attempt_id": order.attempt_id, **validation.model_dump()})
                acceptance = result.acceptance
                if not task.get("verifies_issue") and validation.outcome != "accepted":
                    acceptance = validation.outcome
                task.update(execution_status=result.execution_status, acceptance=acceptance,
                            validity="current", report=result.report, design=result.design)
                if task["kind"] == "compute" and result.execution_status == "succeeded":
                    available_refs = ids | input_evidence
                    supplied = {}
                    for proof in result.report.get("requirement_evidence", []):
                        supplied.setdefault(proof["requirement_id"], set()).update(proof["evidence_refs"])
                    for rid in task["requirement_ids"]:
                        refs = supplied.get(rid, set())
                        if not refs or not refs <= available_refs:
                            acceptance = "inconclusive"
                            task["acceptance"] = acceptance
                            report = IssueReport(task_id=order.task_id, category="coverage",
                                message=f"要求 {rid} 缺少有效登记证据映射", evidence_refs=sorted(ids))
                            issue = self._record_issue(state, report, "confirmed", producer_attempt=order.attempt_id)
                            self._confirm_issue(state, issue, refute=False)
                for report in result.issues:
                    if not set(report.evidence_refs) <= (ids | input_evidence):
                        raise ValueError("执行问题引用了冻结输入之外的证据")
                    if result.execution_status != "succeeded":
                        report = IssueReport(task_id=order.task_id, category="execution", message=report.message, evidence_refs=report.evidence_refs)
                    self._record_issue(state, report, "suspected" if report.category not in {"execution", "service", "evidence"} else "confirmed",
                                       producer_attempt=order.attempt_id)
                    if report.category == "evidence":
                        self._confirm_issue(state, next(i for i in state["issues"] if i["producer_attempt"] == order.attempt_id and i["message"] == report.message), refute=False)
                        acceptance = "inconclusive"
                if task.get("verifies_issue"):
                    issue = next(i for i in state["issues"] if i["id"] == task["verifies_issue"])
                    issue["evidence_refs"] = sorted(set(issue["evidence_refs"]) | {rid for c in validation.checks for rid in c.evidence_refs})
                    if validation.outcome == "rejected":
                        self._confirm_issue(state, issue)
                        # Invalidating the disputed task also invalidates its investigation
                        # dependency. Preserve this completed counterexample as history.
                        task.update(acceptance="accepted", validity="current", active_attempt=order.attempt_id,
                                    execution_status="succeeded")
                    elif validation.outcome == "accepted" and result.execution_status == "succeeded":
                        issue["status"] = "dismissed"
                        task["acceptance"] = "accepted"
                    else:
                        issue["status"] = "inconclusive"
                        task["acceptance"] = "inconclusive"
                    task["retired"] = True
                else:
                    for check in validation.checks:
                        if check.outcome == "passed":
                            continue
                        owner = state["assumptions"].get(check.owner_id) if check.owner_kind == "assumption" else None
                        target = owner["task_ids"][0] if owner else order.task_id
                        report = IssueReport(task_id=target, category="assumption" if owner else "domain",
                            message=check.message, evidence_refs=check.evidence_refs,
                            assumption_id=check.owner_id if owner else None, check_ids=[check.check_id])
                        issue = self._record_issue(state, report, "confirmed", producer_attempt=order.attempt_id)
                        self._confirm_issue(state, issue, refute=check.outcome == "failed")
                attempt["acceptance"] = task["acceptance"] if task.get("verifies_issue") else acceptance
                self._resolve_repairs(state)
                if any(i["blocking"] and i["status"] not in {"resolved", "dismissed"} for i in state["issues"]):
                    state["outcome"] = "blocked"
            else:
                attempt["validity"] = "stale"
                if task["active_attempt"] == order.attempt_id:
                    task["revision"] += 1
                    state["revision"] += 1
                    self._invalidate(state, {order.task_id}, "执行期间输入已改变")
            self._save(db, state, "attempt_committed", receipt)
            return receipt

    @staticmethod
    def _record_issue(state, report, status, producer_attempt=None):
        if report.task_id not in state["tasks"]:
            raise ValueError("问题指向未知任务")
        task = state["tasks"][report.task_id]
        known = set(state["evidence"])
        if not set(report.evidence_refs) <= known:
            raise ValueError("问题引用了未知证据")
        if report.assumption_id and report.assumption_id not in state["assumptions"]:
            raise ValueError("问题指向未知假设")
        identity = digest({**report.model_dump(), "revision": task["revision"]})[:24]
        previous = next((i for i in state["issues"] if i["id"] == identity), None)
        if previous:
            if report.category in {"writing", "service"} and previous["status"] in {"resolved", "dismissed"}:
                previous["status"] = status
            return previous
        issue = Issue(**report.model_dump(), id=identity, task_revision=task["revision"],
            producer_attempt=producer_attempt, status=status,
            assumption_revision=state["assumptions"][report.assumption_id]["revision"] if report.assumption_id else None,
            action="retry" if report.category in {"execution", "service"} else "rewrite" if report.category == "writing" else "verify",
            blocking=report.category != "writing").model_dump()
        state["issues"].append(issue)
        return issue

    @classmethod
    def _confirm_issue(cls, state, issue, refute=True):
        issue.update(status="confirmed", action="recompute")
        roots = {issue["task_id"]}
        if issue["assumption_id"] and refute:
            assumption = state["assumptions"][issue["assumption_id"]]
            assumption["status"] = "refuted"
            roots |= set(assumption["task_ids"])
        affected = cls._invalidate(state, roots, issue["message"])
        issue["repair_targets"] = {key: state["tasks"][key]["revision"] for key in affected if not state["tasks"][key]["retired"]}
        issue["action"] = "recompute"

    @staticmethod
    def _resolve_repairs(state):
        for issue in state["issues"]:
            if issue["status"] != "repairing":
                continue
            if all(state["tasks"][key]["acceptance"] == "accepted" and
                   state["tasks"][key]["validity"] == "current" and state["tasks"][key]["revision"] >= revision
                   for key, revision in issue["repair_targets"].items()):
                issue["status"] = "resolved"

    def report_issues(self, reports, source):
        with self._transaction() as db:
            state = self._read(db)
            if source == "sections":
                for issue in state["issues"]:
                    if issue["category"] == "writing" and issue.get("section") != "paper":
                        issue["status"] = "resolved"
            recorded = [self._record_issue(state, report, "confirmed" if report.category in {"writing", "service"} else "suspected")
                        for report in reports]
            self._save(db, state, "issues_reported", {"source": source, "issues": recorded})
            return [i for i in recorded if i["status"] not in {"resolved", "dismissed"}]

    @staticmethod
    def delivery_ready(state):
        return all(t["retired"] or (t["acceptance"] == "accepted" and t["validity"] == "current") for t in state["tasks"].values()) and not any(
            i["blocking"] and i["status"] not in {"resolved", "dismissed"} for i in state["issues"])

    def schedule_investigations(self):
        with self._transaction() as db:
            state = self._read(db)
            created = []
            changed = False
            for issue in state["issues"]:
                if issue["status"] != "suspected" or any(t.get("verifies_issue") == issue["id"] for t in state["tasks"].values()):
                    continue
                candidates = []
                for key, original in state["tasks"].items():
                    if original["kind"] == "verify":
                        continue
                    for rule in protocol(state, key):
                        related = (rule["owner_kind"] == "assumption" and rule["owner_id"] == issue["assumption_id"] and
                                   issue["task_id"] in state["assumptions"][rule["owner_id"]]["task_ids"]) if issue["assumption_id"] else rule["check"]["left"]["task_id"] == issue["task_id"]
                        if related and (not issue["check_ids"] or rule["check"]["id"] in issue["check_ids"]):
                            candidates.append(rule)
                if not candidates:
                    issue["status"] = "inconclusive"
                    changed = True
                    continue
                sid = f"Q{max((int(re.match(r'Q([0-9]+)', key)[1]) for key in state['tasks']), default=0) + 1}"
                deps = {rule["check"]["left"]["task_id"] for rule in candidates}
                deps |= {rule["check"]["right"]["task_id"] for rule in candidates if rule["check"].get("right")}
                sp = {"id": sid, "title": "独立验证研究疑点", "question": issue["message"] +
                    "\n独立重算以下检查的左操作数，使用完全相同的登记名称；条件不成立也是有效验证结果。\n" + json.dumps(candidates, ensure_ascii=False),
                    "kind": "verify", "depends_on": sorted(deps), "deliverables": []}
                state["tasks"][sid] = {"task_id": sid, "revision": 1, "objective": sp["question"], "subproblem": sp,
                    "dependencies": sorted(deps), "execution_status": "pending", "acceptance": "pending", "validity": "current",
                    "active_attempt": None, "report": None, "design": None, "requirement_ids": [],
                    "assumption_ids": [], "kind": "verify", "retired": False,
                    "verifies_issue": issue["id"], "verification_protocol": candidates}
                created.append(sid)
            if created or changed:
                self._save(db, state, "investigations_scheduled", {"tasks": created})
            return created

    @staticmethod
    def _active_registry(state):
        accepted = {t["active_attempt"] for t in state["tasks"].values()
                    if t["kind"] != "verify" and t["acceptance"] == "accepted" and t["validity"] == "current"}
        results = [e["payload"] for e in state["evidence"].values() if e["producer_attempt"] in accepted]
        claims = [c["payload"] for c in state["claims"].values() if c["producer_attempt"] in accepted]
        return {"results": results, "claims": claims, "results_by_id": {r["id"]: r for r in results}}

    def registry_payload(self, snapshot=None):
        state = snapshot if snapshot is not None else self.snapshot()
        payload = self._active_registry(state) if state else {"results": [], "claims": []}
        return {key: payload[key] for key in ("results", "claims")}

    @staticmethod
    def _invalidate(state, affected, reason):
        affected = set(affected)
        if not affected:
            return []
        while True:
            more = {key for key, task in state["tasks"].items() if set(task["dependencies"]) & affected}
            if more <= affected:
                break
            affected |= more
        for key in affected:
            task = state["tasks"][key]
            if task["retired"]:
                continue
            if task["active_attempt"]:
                state["attempts"][task["active_attempt"]]["validity"] = "stale"
            task.update(execution_status="pending", acceptance="pending", validity="stale", active_attempt=None,
                        report=None, design=None, reason=reason)
        for key, section in state["sections"].items():
            if not key.startswith("solve:") or set(section["task_refs"]) & affected:
                section["validity"] = "stale"
        state["outcome"] = "running"
        return sorted(affected)

    def apply_patch(self, patch: PlanPatch):
        signature = digest(patch.model_dump())
        with self._transaction() as db:
            old = db.execute("SELECT digest, response FROM commands WHERE research_id=? AND id=?",
                             (self.research_id, patch.command_id)).fetchone()
            if old:
                if old[0] != signature:
                    raise ResearchConflict("command_id 已被其他命令使用")
                return json.loads(old[1])
            state = self._read(db)
            if state["revision"] != patch.base_revision:
                raise ResearchConflict("研究版本已改变，请刷新后重试")
            issues = [i for i in state["issues"] if i["id"] in patch.issue_ids]
            if len(issues) != len(set(patch.issue_ids)) or any(i["status"] != "confirmed" or i["action"] != "recompute" for i in issues):
                raise ResearchConflict("计划只能修复当前已确认的问题")
            for issue in issues:
                if not any(self._reaches(state, change.task_id, issue["task_id"]) for change in patch.changes) and not any(
                    change.assumption_id == issue["assumption_id"] for change in patch.assumptions):
                    raise ValueError("计划没有修复触发问题的任务或其上游")
            if patch.requirements:
                if not patch.amendment:
                    raise ValueError("新增要求必须来自明确的用户修改")
                known = {r["id"] for r in state["spec"]["requirements"]}
                for requirement in patch.requirements:
                    if requirement.id in known or requirement.source_quote not in patch.amendment:
                        raise ValueError("新增要求编号或来源无效，原题要求不能覆盖")
                    state["spec"]["requirements"].append({**requirement.model_dump(), "revision": 1})
                    known.add(requirement.id)
                state["spec"]["amendments"].append(patch.amendment)
            assumption_roots = set()
            for change in patch.assumptions:
                previous = state["assumptions"].get(change.assumption_id)
                if not previous or previous["revision"] != change.base_revision:
                    raise ResearchConflict("假设版本已改变")
                if previous["status"] != "refuted" or not any(i["assumption_id"] == change.assumption_id for i in issues):
                    raise ValueError("假设修订必须有已确认的反证问题")
                if not set(change.task_ids) <= set(state["tasks"]):
                    raise ValueError("假设作用范围包含未知任务")
                state["assumption_history"].append(dict(previous))
                assumption_roots |= set(previous["task_ids"]) | set(change.task_ids)
                state["assumptions"][change.assumption_id] = {"id": change.assumption_id, "revision": previous["revision"] + 1,
                    "statement": change.statement, "task_ids": change.task_ids, "checks": [c.model_dump() for c in change.checks], "status": change.status}
            ids = [c.task_id for c in patch.changes]
            if len(set(ids)) != len(ids):
                raise ValueError("计划修订包含重复任务")
            for change in patch.changes:
                if change.action == "add":
                    if change.kind == "verify" or not change.requirement_ids:
                        raise ValueError("新增研究任务必须绑定现有要求，验证任务由问题协议调度")
                    if change.task_id in state["tasks"] or not re.fullmatch(r"Q[0-9]+[a-z]?", change.task_id):
                        raise ValueError("新增任务需要未使用的 Q 编号")
                    sp = {"id": change.task_id, "title": change.title or change.task_id,
                          "kind": change.kind, "question": change.objective,
                          "deliverables": [], "depends_on": change.dependencies or []}
                    state["tasks"][change.task_id] = {"task_id": change.task_id, "revision": 0,
                        "objective": change.objective, "subproblem": sp,
                        "dependencies": change.dependencies or [], "execution_status": "pending",
                        "acceptance": "pending", "validity": "current", "active_attempt": None,
                        "report": None, "design": None, "requirement_ids": [], "assumption_ids": [],
                        "kind": change.kind, "retired": False}
                if change.task_id not in state["tasks"]:
                    raise ValueError("计划修订指向未知任务")
                task = state["tasks"][change.task_id]
                if change.requirement_ids is not None:
                    known = {r["id"] for r in state["spec"]["requirements"]}
                    if not set(task["requirement_ids"]) <= set(change.requirement_ids) or not set(change.requirement_ids) <= known:
                        raise ValueError("计划不能移除任务已覆盖的要求或引用未知要求")
                    task["requirement_ids"] = change.requirement_ids
                    for requirement in state["spec"]["requirements"]:
                        if requirement["id"] in change.requirement_ids and change.task_id not in requirement["task_ids"]:
                            requirement["task_ids"].append(change.task_id)
                task["revision"] += 1
                task["objective"] = change.objective
                task["subproblem"] = {**task["subproblem"], "question": change.objective}
                if change.dependencies is not None:
                    task["dependencies"] = change.dependencies
                    task["subproblem"]["depends_on"] = change.dependencies
            for key, task in state["tasks"].items():
                task["assumption_ids"] = [aid for aid, assumption in state["assumptions"].items()
                                          if key in assumption["task_ids"] and assumption["status"] != "retired"]
                if key in assumption_roots and key not in ids:
                    task["revision"] += 1
            validate_dependencies(state["tasks"])
            self._validate_contracts(state)
            affected = self._invalidate(state, set(ids) | assumption_roots, patch.reason)
            changed_assumptions = {change.assumption_id for change in patch.assumptions}
            for task in state["tasks"].values():
                if task.get("verifies_issue"):
                    issue = next(i for i in state["issues"] if i["id"] == task["verifies_issue"])
                    if issue["assumption_id"] in changed_assumptions:
                        # A frozen countercheck of the old premise cannot refute
                        # its replacement. Fresh scope acceptance closes the repair.
                        task["retired"] = True
            for issue in state["issues"]:
                related_assumption = issue["assumption_id"] in changed_assumptions and issue["status"] not in {"resolved", "dismissed"}
                if issue not in issues and not related_assumption and not (patch.amendment and issue["task_id"] in affected and issue["status"] in {"confirmed", "inconclusive"}):
                    continue
                if issue["assumption_id"] and state["assumptions"][issue["assumption_id"]]["status"] == "refuted":
                    continue
                issue["status"] = "repairing"
                issue["repair_targets"] = {key: state["tasks"][key]["revision"] for key in affected if not state["tasks"][key]["retired"]}
            state["revision"] += 1
            receipt = {"revision": state["revision"], "affected": affected, "commit_seq": state["commit_seq"] + 1}
            self._save(db, state, "plan_patched", {"patch": patch.model_dump(), **receipt})
            db.execute("INSERT INTO commands VALUES (?, ?, ?, ?)",
                       (self.research_id, patch.command_id, signature, json.dumps(receipt)))
            return receipt

    @staticmethod
    def _reaches(state, upstream, target):
        if upstream == target:
            return True
        return any(ResearchStore._reaches(state, upstream, dep) for dep in state["tasks"][target]["dependencies"])

    @staticmethod
    def _validate_contracts(state):
        regular = [t["subproblem"] for t in state["tasks"].values() if t["kind"] != "verify"]
        analysis = {"subproblems": regular,
            "requirements": [{k: v for k, v in r.items() if k != "revision"} for r in state["spec"]["requirements"]],
            "assumptions": [{k: v for k, v in a.items() if k not in {"revision", "status"}}
                            for a in state["assumptions"].values()]}
        validate_spec(state["spec"]["requirement"] + "\n" + "\n".join(state["spec"]["amendments"]), analysis)
        for task in state["tasks"].values():
            if task["kind"] == "verify":
                continue
            for rule in protocol(state, task["task_id"]):
                right = rule["check"].get("right")
                if right and not ResearchStore._reaches(state, right["task_id"], task["task_id"]):
                    raise ValueError("跨任务验收操作数必须声明任务依赖")

    def reserve_planning_call(self):
        with self._transaction() as db:
            state = self._read(db)
            if state["planning_calls"] >= 2 or len(state["attempts"]) >= state["max_total_attempts"]:
                return False
            state["planning_calls"] += 1
            self._save(db, state, "planning_started", {"call": state["planning_calls"]})
            return True

    def record_proposal(self, proposal):
        signature = digest(proposal)
        with self._transaction() as db:
            state = self._read(db)
            if signature in state["proposal_hashes"]:
                return False
            state["proposal_hashes"].append(signature)
            self._save(db, state, "proposal_recorded", proposal)
            return True

    def set_outcome(self, outcome):
        with self._transaction() as db:
            state = self._read(db)
            if state["outcome"] != outcome:
                state["outcome"] = outcome
                self._save(db, state, "outcome_changed", {"outcome": outcome})

    def load_revision_command(self, command_id, base_revision, change, scope):
        with closing(self._connect()) as db:
            row = db.execute("SELECT digest, response FROM commands WHERE research_id=? AND id=?",
                             (self.research_id, "revision:" + command_id)).fetchone()
            if not row:
                return None
            if row[0] != digest({"base_revision": base_revision, "change": change, "scope": scope}):
                raise ResearchConflict("command_id 已被其他命令使用")
            return PlanPatch.model_validate_json(row[1])

    def apply_revision_command(self, command_id, base_revision, change, scope, patch):
        from .models import PlanPatch
        signature = digest({"base_revision": base_revision, "change": change, "scope": scope})
        # The command's original patch is retained; a retry must not append the
        # user's change to the already-revised objective a second time.
        key = "revision:" + command_id
        with self._transaction() as db:
            row = db.execute("SELECT digest, response FROM commands WHERE research_id=? AND id=?",
                             (self.research_id, key)).fetchone()
            if row:
                if row[0] != signature:
                    raise ResearchConflict("command_id 已被其他命令使用")
                patch = PlanPatch.model_validate_json(row[1])
            else:
                db.execute("INSERT INTO commands VALUES (?, ?, ?, ?)",
                           (self.research_id, key, signature, patch.model_dump_json()))
        return self.apply_patch(patch)

    def recover(self):
        """Called only by the single work coordinator after its previous run ended.

        An interrupted kernel cannot be restored from a graph checkpoint. Failed
        attempts remain in history; retries acquire new leases and attempt IDs.
        """
        with self._transaction() as db:
            state = self._read(db)
            if not state:
                return
            for task in state["tasks"].values():
                if task.get("verifies_issue"):
                    issue = next(i for i in state["issues"] if i["id"] == task["verifies_issue"])
                    if issue["status"] == "inconclusive":
                        task["retired"] = False
                        issue["status"] = "suspected"
            pending = {key for key, task in state["tasks"].items()
                       if not task["retired"] and (task["acceptance"] != "accepted" or task["validity"] != "current")}
            for attempt in state["attempts"].values():
                if attempt["execution_status"] == "running":
                    attempt["execution_status"] = "cancelled"
            self._invalidate(state, pending, "从已提交业务状态恢复")
            for issue in state["issues"]:
                if issue["category"] == "service":
                    issue["status"] = "resolved"  # The graph will rerun the failed audit before delivery.
                elif issue["action"] == "retry" and issue["status"] == "confirmed":
                    issue["status"] = "repairing"
                    issue["repair_targets"] = {issue["task_id"]: state["tasks"][issue["task_id"]]["revision"]}
            self._save(db, state, "recovered", {"affected": sorted(pending)})

    def save_sections(self, sections: dict, issues: list, contexts: dict | None = None):
        with self._transaction() as db:
            state = self._read(db)
            for key, text in sections.items():
                old = state["sections"].get(key)
                context_hash = (contexts or {}).get(key)
                if old and old["text"] == text and old["validity"] == "current" and old.get("context_hash") == context_hash:
                    continue
                refs = [key.split(":", 1)[1]] if key.startswith("solve:") else list(state["tasks"])
                state["sections"][key] = {"revision": (old or {}).get("revision", 0) + 1,
                    "text": text, "content_hash": digest(text), "context_hash": context_hash,
                    "evidence_refs": re.findall(r"\{\{[RF]:([RF][0-9]+)\}\}", text),
                    "task_refs": refs, "validity": "current"}
            state["outcome"] = ("needs_attention" if issues else "completed") if self.delivery_ready(state) else "blocked"
            self._save(db, state, "sections_committed", {"versions": {key: state["sections"][key] for key in sections}, "issues": issues})

    def publish_paper(self, text: str):
        """Keep generated versions and preserve a manually edited active paper."""
        with self._transaction() as db:
            state = self._read(db)
            if not self.delivery_ready(state):
                raise ResearchConflict("研究仍有阻塞问题，不能发布论文")
            relative = f".system/research/{self.research_id}/papers/{state['commit_seq'] + 1}.md"
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            paper = self.root / "paper.md"
            existing = paper.read_text(encoding="utf-8") if paper.exists() else None
            previous = state.get("paper_hash")
            conflict = previous is not None and existing is not None and digest(existing) != previous
            state["paper_version"] = relative
            state["paper_conflict"] = conflict
            if conflict:
                state["outcome"] = "needs_attention"
                report = IssueReport(task_id=next(t["task_id"] for t in state["tasks"].values() if t["kind"] != "verify"),
                    category="writing", section="paper", message=f"paper.md 有人工修改，自动新稿保存在 {relative}")
                self._record_issue(state, report, "confirmed")
            else:
                temp = paper.with_suffix(".md.tmp")
                temp.write_text(text, encoding="utf-8")
                temp.replace(paper)
                state["paper_hash"] = digest(text)
                state["exports"] = []
            self._save(db, state, "paper_published", {"path": relative, "content_hash": digest(text), "conflict": conflict})
            return relative if conflict else "paper.md", conflict

    def register_exports(self, paths):
        with self._transaction() as db:
            state = self._read(db)
            state["exports"] = list(paths)
            self._save(db, state, "exports_committed", {"paths": paths, "paper_hash": state.get("paper_hash")})
