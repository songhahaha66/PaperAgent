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
from .models import PlanPatch, ResearchConflict, WorkerResult, WorkOrder

DB_PATH = ".system/research.sqlite"


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


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
            tasks = {sp["id"]: {
                "task_id": sp["id"], "revision": 1, "objective": sp["question"],
                "dependencies": sp.get("depends_on") or [], "subproblem": sp,
                "execution_status": "pending", "acceptance": "pending", "validity": "current",
                "active_attempt": None, "report": None, "design": None,
            } for sp in analysis["subproblems"]}
            validate_dependencies(tasks)
            state = {"schema_version": 1, "engine_version": ENGINE_VERSION,
                     "research_id": self.research_id, "revision": 1, "commit_seq": 0,
                     "spec": {"requirement": requirement, "assumptions": analysis.get("assumptions", [])},
                     "tasks": tasks, "attempts": {}, "evidence": {}, "claims": {},
                     "sections": {}, "issues": [], "outcome": "running", "max_attempts": max_attempts,
                     "planning_calls": 0, "proposal_hashes": [], "max_total_attempts": len(tasks) * max_attempts + 6}
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
        return refs

    def refresh_inputs(self):
        files = self.file_inputs()
        with self._transaction() as db:
            state = self._read(db)
            changed = set()
            for key, task in state["tasks"].items():
                attempt = state["attempts"].get(task["active_attempt"])
                if task["acceptance"] == "accepted" and attempt:
                    previous = {k: v for k, v in attempt["input_refs"].items() if not k.startswith("task:")}
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
            if task["execution_status"] != "pending":
                raise ResearchConflict(f"任务 {task_id} 已领取或需要显式重试")
            if any(state["tasks"][dep]["acceptance"] != "accepted" or
                   state["tasks"][dep]["validity"] != "current" for dep in task["dependencies"]):
                raise ResearchConflict(f"任务 {task_id} 的依赖尚未验收")
            attempts = [a for a in state["attempts"].values() if a["task_id"] == task_id and a["revision"] == task["revision"]]
            if len(attempts) >= state["max_attempts"] or len(state["attempts"]) >= state["max_total_attempts"]:
                raise ResearchConflict(f"任务 {task_id} 已达到尝试预算")
            refs = self._inputs(state, task, files)
            order = WorkOrder(attempt_id=uuid.uuid4().hex, task_id=task_id, revision=task["revision"],
                              input_digest=digest(refs), input_refs=refs, lease=uuid.uuid4().hex)
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
            if any(attempt[key] != getattr(order, key) for key in ("task_id", "revision", "lease", "input_digest", "input_refs")):
                raise ResearchConflict("执行权或冻结输入不匹配")
            current = (task["revision"] == order.revision and task["active_attempt"] == order.attempt_id
                       and attempt["execution_status"] == "running"
                       and self._inputs(state, task, files) == order.input_refs)
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
                for claim in result.claims:
                    if claim["id"] in state["claims"] or claim["subproblem"] != order.task_id:
                        raise ValueError("结论编号重复或不属于当前任务")
                    if not set(claim.get("evidence", [])) <= (ids | set(self._active_registry(state)["results_by_id"])):
                        raise ValueError("结论引用了不可用证据")
                for item in result.results:
                    state["evidence"][item["id"]] = {"payload": item, "content_hash": digest(item),
                        "artifact_hash": self._artifact_hash(item), "producer_attempt": order.attempt_id}
                for item in result.claims:
                    state["claims"][item["id"]] = {"payload": item, "producer_attempt": order.attempt_id}
                task.update(execution_status=result.execution_status, acceptance=result.acceptance,
                            validity="current", report=result.report, design=result.design)
                state["issues"] = [i for i in state["issues"] if i.get("task_id") != order.task_id]
                state["issues"].extend({"task_id": order.task_id, "category": "validation", "blocking": True,
                                        "message": message} for message in result.issues)
                if result.acceptance != "accepted":
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
    def _active_registry(state):
        accepted = {t["active_attempt"] for t in state["tasks"].values()
                    if t["acceptance"] == "accepted" and t["validity"] == "current"}
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
            ids = [c.task_id for c in patch.changes]
            if len(set(ids)) != len(ids):
                raise ValueError("计划修订包含重复任务")
            for change in patch.changes:
                if change.action == "add":
                    if change.task_id in state["tasks"] or not re.fullmatch(r"Q[0-9]+[a-z]?", change.task_id):
                        raise ValueError("新增任务需要未使用的 Q 编号")
                    sp = {"id": change.task_id, "title": change.title or change.task_id,
                          "kind": change.kind, "question": change.objective,
                          "deliverables": [], "depends_on": change.dependencies or []}
                    state["tasks"][change.task_id] = {"task_id": change.task_id, "revision": 0,
                        "objective": change.objective, "subproblem": sp,
                        "dependencies": change.dependencies or [], "execution_status": "pending",
                        "acceptance": "pending", "validity": "current", "active_attempt": None,
                        "report": None, "design": None}
                if change.task_id not in state["tasks"]:
                    raise ValueError("计划修订指向未知任务")
                task = state["tasks"][change.task_id]
                task["revision"] += 1
                task["objective"] = change.objective
                task["subproblem"] = {**task["subproblem"], "question": change.objective}
                if change.dependencies is not None:
                    task["dependencies"] = change.dependencies
                    task["subproblem"]["depends_on"] = change.dependencies
            validate_dependencies(state["tasks"])
            affected = self._invalidate(state, ids, patch.reason)
            state["revision"] += 1
            receipt = {"revision": state["revision"], "affected": affected, "commit_seq": state["commit_seq"] + 1}
            self._save(db, state, "plan_patched", {"patch": patch.model_dump(), **receipt})
            db.execute("INSERT INTO commands VALUES (?, ?, ?, ?)",
                       (self.research_id, patch.command_id, signature, json.dumps(receipt)))
            return receipt

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
            pending = {key for key, task in state["tasks"].items()
                       if task["acceptance"] != "accepted" or task["validity"] != "current"}
            for attempt in state["attempts"].values():
                if attempt["execution_status"] == "running":
                    attempt["execution_status"] = "cancelled"
            self._invalidate(state, pending, "从已提交业务状态恢复")
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
            state["outcome"] = "needs_attention" if issues else "completed"
            self._save(db, state, "sections_committed", {"versions": {key: state["sections"][key] for key in sections}, "issues": issues})

    def publish_paper(self, text: str):
        """Keep generated versions and preserve a manually edited active paper."""
        with self._transaction() as db:
            state = self._read(db)
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
                state["issues"].append({"task_id": "paper", "category": "edit_conflict", "blocking": True,
                    "message": f"paper.md 有人工修改，自动新稿保存在 {relative}"})
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
