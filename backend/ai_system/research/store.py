"""SQLite is authoritative; checkpoints and results.json are projections.

Each command holds BEGIN IMMEDIATE through validation and publication. Documents
are intentionally coarse in this first schema: old revisions and attempts remain
in the journal, while reads see a single committed, consistent snapshot.
"""

from ai_system.research.domain import findings as research_findings
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager, closing
from pathlib import Path

from . import ENGINE_VERSION, DEFAULT_PLANNING_CALL_LIMIT
from .models import Assumption, DomainCheck, PlanPatch, ResearchConflict, WorkerResult, WorkOrder, Issue, IssueReport, ValidationRecord, ContentFeedback, RuntimeFault
from .validation import bind_requirements, validate_spec, protocol, evaluate, fingerprint, report_fingerprint, requirement_evidence_gaps, needs_owner_review
from .assumptions import assumption_id, premise_definition, assumption_scope

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
    def __init__(self, workspace: str | Path, research_id: str, *, read_only: bool = False, experience_scope=None):
        self.root = Path(workspace).resolve()
        self.path = self.root / DB_PATH
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", research_id):
            raise ValueError("研究标识格式错误")
        self.research_id = research_id
        self.read_only = read_only
        self.experience_scope = experience_scope
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
        from .migration import migrate_workspace
        migrate_workspace(self)

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
        state = json.loads(row[0]) if row else None
        if state and state.get("schema_version") != 3:
            raise ValueError("研究数据尚未迁移，请先运行 python -m services.migrate_data")
        return state

    def snapshot(self):
        with closing(self._connect()) as db:
            return self._read(db)

    def _save(self, db, state, kind, payload):
        state["commit_seq"] += 1
        db.execute("INSERT OR REPLACE INTO research VALUES (?, ?)",
                   (self.research_id, json.dumps(state, ensure_ascii=False)))
        db.execute("INSERT INTO journal VALUES (?, ?, ?, ?)",
                   (self.research_id, state["commit_seq"], kind, json.dumps(payload, ensure_ascii=False)))

    def initialize(self, requirement: str, analysis: dict | None = None, max_attempts: int = 3, *, output_mode="markdown"):
        with self._transaction() as db:
            state = self._read(db)
            if state:
                return state
            # Saved full analyses are accepted only at this import boundary.
            # New runs retain the entire source, without guessing a question graph.
            if analysis is None:
                analysis = {"subproblems": [], "assumptions": [], "requirements": [
                    {"id": "RQ0", "statement": requirement, "source_quote": requirement,
                     "kind": "goal", "task_ids": [], "checks": []}]}
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
            state = {"schema_version": 3, "engine_version": ENGINE_VERSION,
                     "research_id": self.research_id, "revision": 1, "commit_seq": 0,
                     "spec": {"requirement": requirement, "amendments": [], "output_mode": output_mode,
                              "requirements": [{**r.model_dump(), "revision": 1} for r in requirements]},
                     "assumptions": {a.id: {**a.model_dump(), "revision": 1, "status": "active", "source": "analysis", "producer_attempt": None} for a in assumptions},
                     "assumption_history": [], "validations": [],
                     "tasks": tasks, "attempts": {}, "evidence": {}, "claims": {},
                     "sections": {}, "feedback": [], "faults": [], "legacy_records": [], "delivery_stages": {}, "outcome": "running", "max_attempts": max_attempts,
                     "materials": {"inputs": self.file_inputs(), "profile": ""},
                     "planning_calls": 0, "proposal_hashes": [], "max_total_attempts": len(tasks) * max_attempts + 6}
            self._validate_contracts(state)
            self._bind_assumption_inputs(state)
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

    def skill_preferences(self):
        from ai_system.modules.methods import DEFAULT_SKILL_POLICY
        with closing(self._connect()) as db:
            row = db.execute("SELECT response FROM commands WHERE research_id='workspace-skills' AND id='policy'").fetchone()
        return json.loads(row[0]) if row else {'version': 0, 'policy': dict(DEFAULT_SKILL_POLICY)}

    def set_skill_preferences(self, policy, base_version):
        from ai_system.modules.methods import validate_skill_policy
        policy = validate_skill_policy(policy)
        with self._transaction() as db:
            row = db.execute("SELECT response FROM commands WHERE research_id='workspace-skills' AND id='policy'").fetchone()
            previous = json.loads(row[0]) if row else self.skill_preferences()
            if previous['version'] != base_version:
                raise ResearchConflict('技能配置已改变，请刷新后重试')
            if previous['policy'] == policy:
                return {**previous, 'affected': []}
            state = self._read(db)
            affected = []
            if state:
                roots = {key for key, task in state['tasks'].items() if not task['retired'] and task['kind'] != 'writing'}
                affected = self._invalidate(state, roots, '科研技能配置已改变，使用当前选择重新设计和求解')
                for key in affected:
                    state['tasks'][key]['revision'] += 1
                state['revision'] += 1
                self._save(db, state, 'skills_changed', {'policy': policy, 'affected': affected})
            response = {'version': base_version + 1, 'policy': policy}
            db.execute('INSERT OR REPLACE INTO commands VALUES (?, ?, ?, ?)',
                       ('workspace-skills', 'policy', digest(policy), json.dumps(response, ensure_ascii=False)))
            return {**response, 'affected': affected}

    def file_inputs(self):
        refs = {}
        preferences = self.skill_preferences()
        if preferences['version']:
            refs['skills:policy'] = digest(preferences['policy'])
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

    def claim(self, task_id: str, *, review_required=False) -> WorkOrder:
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
            if any(i["blocking"] and i["status"] == "confirmed" and task_id in i["repair_targets"] for i in research_findings(state)):
                raise ResearchConflict("任务需要先提交修复计划")
            attempts = [a for a in state["attempts"].values() if a["task_id"] == task_id and a["revision"] == task["revision"]]
            if len(attempts) >= state["max_attempts"] or len(state["attempts"]) >= state["max_total_attempts"]:
                raise ResearchConflict(f"任务 {task_id} 已达到尝试预算")
            for requirement in state["spec"]["requirements"]:
                if requirement['kind']=='constraint' and task_id in requirement['task_ids'] and not any(
                        c['left']['task_id']==task_id for c in requirement['checks']):
                    raise ResearchConflict('相关原题约束尚未绑定本实验的操作化检查')
            refs = self._inputs(state, task, files)
            order = WorkOrder(attempt_id=uuid.uuid4().hex, task_id=task_id, revision=task["revision"],
                              input_digest=digest(refs), input_refs=refs, lease=uuid.uuid4().hex,
                              protocol_hash=digest(protocol(state, task_id)), review_required=review_required,
                              review_issue_ids=[i["id"] for i in research_findings(state) if needs_owner_review(i) and i["task_id"] == task_id])
            from .experience import conditions
            basis = {'question':task['objective'],'protocol':protocol(state,task_id),
                     'conditions':conditions(self.root,state['spec']['requirement']+' '+task['objective'])}
            state["attempts"][order.attempt_id] = {**order.model_dump(), "execution_status": "running", "started_at": time.time(),
                                                 'experience_basis':basis}
            if task.get('experiment'):
                state['attempts'][order.attempt_id]['experiment']=task['experiment']
            task.update(execution_status="running", active_attempt=order.attempt_id)
            self._save(db, state, "claimed", order.model_dump())
            return order

    def commit(self, result: WorkerResult):
        receipt = self._commit(result)
        if self.experience_scope is not None:
            try:
                from types import SimpleNamespace
                from ai_system.modules.registry import invoke_sync
                invoke_sync(SimpleNamespace(experience_scope=self.experience_scope,research=self), 'experience.candidates')
            except Exception:
                import logging
                logging.getLogger(__name__).warning('研究已提交；经验索引未完成，可从经验面板补建')
        return receipt

    def bind_experiences(self, order, refs):
        if not self.experience_scope or not self.experience_scope.valid_refs(refs):
            raise ResearchConflict('经验权限、版本或来源已改变，请重新领取任务')
        with self._transaction() as db:
            state=self._read(db);attempt=state['attempts'][order.attempt_id]
            if attempt['execution_status']!='running' or attempt.get('commit') or attempt['lease']!=order.lease:
                raise ResearchConflict('经验不能加入已结束的尝试')
            combined={ (r['id'],r['stage']):r for r in order.experience_refs }
            combined.update({(r['id'],r['stage']):r for r in refs})
            frozen=list(combined.values())
            if len(frozen)>12:raise ResearchConflict('本次尝试经验引用超过预算')
            if frozen==order.experience_refs:return
            order.experience_refs=frozen
            order.input_digest=digest({'inputs':order.input_refs,'experiences':frozen})
            attempt.update(experience_refs=frozen,input_digest=order.input_digest)
            self._save(db,state,'experiences_bound',{'attempt_id':order.attempt_id,'refs':frozen})

    def _commit(self, result: WorkerResult):
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
            if any(attempt[key] != getattr(order, key) for key in ("task_id", "revision", "lease", "input_digest", "input_refs", "protocol_hash", "review_required", "review_issue_ids")):
                raise ResearchConflict("执行权或冻结输入不匹配")
            if attempt.get('experience_refs',[])!=order.experience_refs:
                raise ResearchConflict('冻结经验引用不匹配')
            experience_current = not order.experience_refs or (self.experience_scope is not None and self.experience_scope.valid_refs(order.experience_refs))
            current = (task["revision"] == order.revision and task["active_attempt"] == order.attempt_id
                       and attempt["execution_status"] == "running"
                       and self._inputs(state, task, files) == order.input_refs
                       and digest(protocol(state, order.task_id)) == order.protocol_hash and experience_current)
            receipt = {"attempt_id": order.attempt_id, "published": current, "commit_seq": state["commit_seq"] + 1}
            if not experience_current:
                receipt['reason']='引用经验已禁用、删除、变更或来源/权限失效，需重新领取任务'
            attempt.update(execution_status=result.execution_status, acceptance=result.acceptance,
                           result_digest=signature, commit=receipt, result=result.model_dump())
            if current:
                if result.acceptance == "accepted" and result.execution_status != "succeeded":
                    raise ValueError("执行失败的结果不能通过验收")
                # Audited intermediate values remain historical evidence even
                # when the worker's registry replaced them during local repair.
                all_evidence = {}
                for item in [*result.results, *[e for r in result.reviews for e in r.evidence]]:
                    if item["id"] in all_evidence and all_evidence[item["id"]] != item:
                        raise ValueError("同一证据编号不能对应不同内容")
                    all_evidence[item["id"]] = item
                for item in all_evidence.values():
                    if item["id"] in state["evidence"] or item["subproblem"] != order.task_id:
                        raise ValueError("结果编号重复或不属于当前任务")
                ids = {r["id"] for r in result.results}
                frozen = {v for k, v in order.input_refs.items() if k.startswith("task:")}
                input_evidence = {rid for rid, entry in state["evidence"].items() if entry["producer_attempt"] in frozen and entry["active"]}
                for claim in result.claims:
                    if claim["id"] in state["claims"] or claim["subproblem"] != order.task_id:
                        raise ValueError("结论编号重复或不属于当前任务")
                    if not set(claim.get("evidence", [])) <= (ids | input_evidence):
                        raise ValueError("结论引用了不可用证据")
                for item in all_evidence.values():
                    state["evidence"][item["id"]] = {"payload": item, "content_hash": digest(item),
                        "artifact_hash": self._artifact_hash(item), "producer_attempt": order.attempt_id, "active": item["id"] in ids}
                introduced = self._register_assumptions(state, result) if result.execution_status == "succeeded" else []
                for item in result.claims:
                    state["claims"][item["id"]] = {"payload": item, "producer_attempt": order.attempt_id,
                        "assumption_refs": {k.removeprefix("assumption:"): int(v) for k, v in order.input_refs.items() if k.startswith("assumption:")}}
                review_clear = self._commit_reviews(state, result, set(all_evidence) | input_evidence, staging=bool(introduced))
                if introduced:
                    attempt.update(acceptance="inconclusive", validity="stale", introduced_assumptions=introduced)
                    receipt.update(published=False, reason="关键假设已登记，按冻结契约重新执行")
                    self._save(db, state, "assumptions_registered", {"attempt_id": order.attempt_id, "assumptions": introduced})
                    return receipt
                validation = evaluate(state, order.task_id, result.results) if result.execution_status == "succeeded" else ValidationRecord(
                    protocol_hash=order.protocol_hash, checks=[], outcome="inconclusive")
                attempt["validation"] = validation.model_dump()
                from .experience import grounded
                attempt['experience_evidence'] = grounded(self.root,state,
                    sorted({ref for check in validation.checks for ref in check.evidence_refs}))
                state["validations"].append({"attempt_id": order.attempt_id, **validation.model_dump()})
                acceptance = result.acceptance
                if not task.get("verifies_issue") and validation.outcome != "accepted":
                    acceptance = validation.outcome
                task.update(execution_status=result.execution_status, acceptance=acceptance,
                            validity="current", report=result.report, design=result.design)
                if task["kind"] == "compute" and result.execution_status == "succeeded":
                    available_refs = ids | input_evidence
                    for message in requirement_evidence_gaps(task["requirement_ids"], result.report.get("requirement_evidence", []), available_refs).values():
                        acceptance = "inconclusive"
                        task["acceptance"] = acceptance
                        report = IssueReport(task_id=order.task_id, category="coverage", message=message, evidence_refs=sorted(ids))
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
                        self._confirm_issue(state, next(i for i in research_findings(state) if i["producer_attempt"] == order.attempt_id and i["message"] == report.message), refute=False)
                        acceptance = "inconclusive"
                if task.get("verifies_issue"):
                    issue = next(i for i in research_findings(state) if i["id"] == task["verifies_issue"])
                    issue["evidence_refs"] = sorted(set(issue["evidence_refs"]) | {rid for c in validation.checks for rid in c.evidence_refs})
                    if validation.outcome == "rejected" and review_clear:
                        self._confirm_issue(state, issue)
                        # Invalidating the disputed task also invalidates its investigation
                        # dependency. Preserve this completed counterexample as history.
                        task.update(acceptance="accepted", validity="current", active_attempt=order.attempt_id,
                                    execution_status="succeeded")
                    elif validation.outcome == "accepted" and result.execution_status == "succeeded" and review_clear:
                        issue["status"] = "dismissed"
                        issue["resolution_attempt"] = order.attempt_id
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
                self._resolve_repairs(state, order.attempt_id)
                if any(i["blocking"] and i["status"] not in {"resolved", "dismissed", "superseded"} for i in research_findings(state)):
                    state["outcome"] = "blocked"
            else:
                attempt["validity"] = "stale"
                if task["active_attempt"] == order.attempt_id:
                    task["revision"] += 1
                    state["revision"] += 1
                    self._invalidate(state, {order.task_id}, "执行期间输入已改变")
            if receipt['published'] and order.experience_refs and not self.experience_scope.valid_refs(order.experience_refs):
                raise ResearchConflict('提交前经验已失效，保留先前结果并重新领取任务')
            self._save(db, state, "attempt_committed", receipt)
            return receipt

    def report_comparison_failure(self,record,order):
        """Only confirmed execution/contract failures enter the existing repair loop."""
        from ai_system.modules.experiments.store import ExperimentStore
        saved=ExperimentStore(self).read(order.task_id,order.revision)
        if not saved or saved['id']!=record['id'] or saved['status']!='no_valid':return
        bad=[r for r in saved['branches'] if r['status'] in {'code_error','contract_error','execution_error','timeout'}]
        if not bad and not saved.get('protocol_error'):return
        with self._transaction() as db:
            state=self._read(db);task=state['tasks'][order.task_id]
            if task['active_attempt']!=order.attempt_id or task['revision']!=order.revision:return
            details='；'.join(f"候选 {r['index']+1}: {r.get('error','契约失败')}" for r in bad)
            message=f"比较 {saved['id']} 的已确认执行/协议问题：{details or saved['reason']}。保留两支记录，不据此判定方法无效。"
            issue=self._record_issue(state,IssueReport(task_id=order.task_id,category='execution',message=message),
                'confirmed',producer_attempt=order.attempt_id,source='comparison')
            self._confirm_issue(state,issue,refute=False)
            self._save(db,state,'comparison_issue',{'comparison_id':saved['id'],'issue_id':issue['id']})

    def _register_assumptions(self, state, result):
        """Stage newly adopted premises; no result is accepted under unfrozen inputs."""
        if not result.assumptions:
            return []
        root = result.order.task_id
        if state["tasks"][root]["kind"] != "compute":
            raise ValueError("验证和写作任务不能引入新的建模前提")
        scope = assumption_scope(state["tasks"], root)
        introduced, seen = [], {}
        for proposal in result.assumptions:
            aid = assumption_id(root, proposal.key)
            definition = premise_definition(root, proposal)
            if aid in seen:
                if seen[aid] != definition:
                    raise ValueError("同次提交的假设键冲突")
                continue
            seen[aid] = definition
            known = state["assumptions"].get(aid)
            if known:
                if "assumption:" + aid not in result.order.input_refs:
                    raise ResearchConflict("已有假设不在本次冻结输入中")
                if any(known[k] != v for k, v in definition.items()) or known["status"] != "active":
                    raise ResearchConflict("不能静默覆盖或复活已有假设，必须通过问题与计划修订")
                continue
            state["assumptions"][aid] = {"id": aid, **definition, "task_ids": scope,
                "revision": 1, "status": "active", "source": proposal.source, "producer_attempt": result.order.attempt_id}
            introduced.append(aid)
        if not introduced:
            return []
        for key in scope:
            task = state["tasks"][key]
            task["assumption_ids"] += [aid for aid in introduced if aid not in task["assumption_ids"]]
            task["revision"] += 1
        self._validate_contracts(state)
        self._invalidate(state, scope, "新增关键假设，需要按冻结契约重算")
        state["tasks"][root]["design"] = result.design
        state["revision"] += 1
        return introduced

    def _commit_reviews(self, state, result, available, *, staging):
        order = result.order
        recorded = []
        allowed = available | {ref for i in research_findings(state) if i["id"] in order.review_issue_ids for ref in i["evidence_refs"]}
        for review in result.reviews:
            for report in review.issues:
                if not set(report.evidence_refs) <= allowed:
                    raise ValueError("局部审查引用了冻结上下文之外的证据")
                issue = self._record_issue(state, report, "confirmed" if report.category == "service" else "suspected",
                    producer_attempt=order.attempt_id, source="local_review",
                    target_revision=order.revision if report.task_id == order.task_id else None)
                issue["blocking"] = True
                recorded.append(issue)
        if staging or result.execution_status != "succeeded":
            return
        fresh = bool(result.reviews and result.reviews[-1].report_hash == report_fingerprint(result.report))
        if order.review_required and not fresh:
            self._record_issue(state, IssueReport(task_id=order.task_id, category="service", message="缺少当前报告的完整局部审查"),
                "confirmed", producer_attempt=order.attempt_id, source="local_review")
        if fresh:
            # Successful auditing closes service faults; scientific findings close
            # only when the current report has been rechecked with no open issues.
            clear = not result.reviews[-1].issues
            for issue in [*recorded, *[i for i in research_findings(state) if i["id"] in order.review_issue_ids]]:
                if not needs_owner_review(issue) or issue["task_id"] != order.task_id:
                    continue
                if clear or issue["category"] == "service" and not any(r.category == "service" for r in result.reviews[-1].issues):
                    issue["status"] = "resolved"
                    issue["resolution_attempt"] = order.attempt_id
        return (fresh and not result.reviews[-1].issues) or (not order.review_required and not result.reviews)

    @staticmethod
    def _record_issue(state, report, status, producer_attempt=None, source="worker", target_revision=None):
        if report.task_id not in state["tasks"]:
            raise ValueError("问题指向未知任务")
        task = state["tasks"][report.task_id]
        known = set(state["evidence"])
        if not set(report.evidence_refs) <= known:
            raise ValueError("问题引用了未知证据")
        if report.assumption_id and report.assumption_id not in state["assumptions"]:
            raise ValueError("问题指向未知假设")
        revision = target_revision if target_revision is not None else task["revision"]
        identity = digest({**report.model_dump(), "revision": revision, "source": source})[:24]
        previous = next((i for i in research_findings(state) if i["id"] == identity), None)
        if previous:
            if (report.category in {"writing", "service"} or report.category=='coverage' and source=='sections' or source == "local_review" and previous["producer_attempt"] != producer_attempt) and previous["status"] in {"resolved", "dismissed", "superseded"}:
                previous["status"] = status
                previous['blocking']=report.category!='writing' or report.section!='paper'
            return previous
        record_type = RuntimeFault if report.category in {'execution', 'service'} else ContentFeedback
        issue = record_type(**report.model_dump(), id=identity, task_revision=revision,
            producer_attempt=producer_attempt, status=status, source=source,
            assumption_revision=state["assumptions"][report.assumption_id]["revision"] if report.assumption_id else None,
            action="retry" if report.category in {"execution", "service"} else "rewrite" if report.category == "writing" else "verify",
            blocking=report.category != "writing" or report.section != "paper").model_dump()
        state["faults" if report.category in {"execution", "service"} else "feedback"].append(issue)
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
    def _resolve_repairs(state, resolution_attempt):
        for issue in research_findings(state):
            if issue["status"] != "repairing":
                continue
            if issue["source"] == "local_review" and issue["action"] != "recompute":
                continue  # Only a fresh review or an independent check closes these findings.
            if all(state["tasks"][key]["acceptance"] == "accepted" and
                   state["tasks"][key]["validity"] == "current" and state["tasks"][key]["revision"] >= revision
                   for key, revision in issue["repair_targets"].items()):
                issue["status"] = "resolved"
                issue["resolution_attempt"] = resolution_attempt

    def report_issues(self, reports, source):
        with self._transaction() as db:
            state = self._read(db)
            if source == "sections":
                for issue in research_findings(state):
                    if issue["source"] == "sections" and issue["category"] in {"writing",'coverage'} and issue.get("section") != "paper":
                        issue["status"] = "resolved"
            recorded = [self._record_issue(state, report, "confirmed" if report.category in {"writing", "service"} else "suspected", source=source)
                        for report in reports]
            for issue in recorded:
                if source=='sections' and issue['category']=='coverage':issue['blocking']=False
            self._save(db, state, "issues_reported", {"source": source, "issues": recorded})
            return [i for i in recorded if i["status"] not in {"resolved", "dismissed", "superseded"}]

    @staticmethod
    def delivery_ready(state):
        # Stopping an attempt never answers the requirement it was meant to
        # investigate. The controller additionally validates substantive coverage.
        covered=all(r['kind']=='deliverable' or any(
            not state['tasks'][key]['retired'] and state['tasks'][key]['acceptance']=='accepted' and
            state['tasks'][key]['validity']=='current' and any(e['active'] and
                e['producer_attempt']==state['tasks'][key]['active_attempt'] for e in state['evidence'].values())
            for key in r['task_ids']) for r in state['spec']['requirements'])
        return covered and all(t["retired"] or (t["acceptance"] == "accepted" and t["validity"] == "current") for t in state["tasks"].values()) and not any(
            i["blocking"] and i["status"] not in {"resolved", "dismissed", "superseded"} for i in research_findings(state))

    def schedule_investigations(self):
        with self._transaction() as db:
            state = self._read(db)
            created = []
            changed = False
            for issue in research_findings(state):
                if issue['source']=='sections' and issue['category']=='coverage':continue
                if issue["status"] != "suspected" or any(t.get("verifies_issue") == issue["id"] for t in state["tasks"].values()):
                    continue
                if not issue["check_ids"]:
                    issue["status"] = "inconclusive"
                    changed = True
                    continue
                candidates = []
                for key, original in state["tasks"].items():
                    if original["kind"] == "verify":
                        continue
                    for rule in protocol(state, key):
                        related = (rule["owner_kind"] == "assumption" and rule["owner_id"] == issue["assumption_id"] and
                                   issue["task_id"] in state["assumptions"][rule["owner_id"]]["task_ids"]) if issue["assumption_id"] else rule["check"]["left"]["task_id"] == issue["task_id"]
                        if related and rule["check"]["id"] in issue["check_ids"]:
                            candidates.append(rule)
                # An omitted/unknown check must not silently become an unrelated
                # task-wide acceptance test. Cited metrics must match the frozen
                # operands, unless an explicitly named assumption binds them.
                cited = {(state["evidence"][ref]["payload"]["subproblem"], state["evidence"][ref]["payload"]["name"])
                         for ref in issue["evidence_refs"]}
                linked = all(any((operand["task_id"], operand["name"]) in cited
                                 for operand in [s.model_dump() for s in DomainCheck.model_validate(rule["check"]).selectors()])
                             for rule in candidates) if cited and not issue["assumption_id"] else True
                if not candidates or {rule["check"]["id"] for rule in candidates} != set(issue["check_ids"]) or not linked:
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
        results = [e["payload"] for e in state["evidence"].values() if e["producer_attempt"] in accepted and e["active"]]
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
        state["delivery_stages"] = {}
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
        payload=patch.model_dump()
        for change in payload['changes']:
            # Empty optional method metadata must not change the identity of a
            # pre-controller revision command restored from schema 3 history.
            if not change.get('method_instruction'):change.pop('method_instruction',None)
        signature = digest(payload)
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
            issues = [i for i in research_findings(state) if i["id"] in patch.issue_ids]
            if len(issues) != len(set(patch.issue_ids)) or any(i["status"] != "confirmed" or i["action"] != "recompute" for i in issues):
                raise ResearchConflict("计划只能修复当前已确认的问题")
            for issue in issues:
                if not any(self._reaches(state, change.task_id, issue["task_id"]) for change in patch.changes) and not any(
                    change.assumption_id == issue["assumption_id"] for change in patch.assumptions):
                    raise ValueError("计划没有修复触发问题的任务或其上游")
            if patch.requirements:
                state['spec']['requirements'] = bind_requirements(state, patch.requirements, patch.amendment)
                if patch.amendment:
                    state['spec']['amendments'].append(patch.amendment)
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
                state["assumptions"][change.assumption_id] = {**previous, "id": change.assumption_id, "revision": previous["revision"] + 1,
                    "statement": change.statement, "task_ids": change.task_ids, "checks": [c.model_dump() for c in change.checks], "status": change.status}
            ids = [c.task_id for c in patch.changes]
            if len(set(ids)) != len(ids):
                raise ValueError("计划修订包含重复任务")
            for change in patch.changes:
                if change.action=='retire':
                    task=state['tasks'].get(change.task_id)
                    if not task or task['kind']=='verify' or task['retired'] or task['acceptance']=='accepted':
                        raise ValueError('只能停止尚未验收的研究分支，已完成成果和独立核查必须保留')
                    task.update(retired=True,reason=patch.reason)
                    for issue in research_findings(state):
                        if issue['task_id']==change.task_id and issue['category']=='execution':
                            issue.update(blocking=False,stop_reason=patch.reason)
                    continue
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
                if change.action=='revise' and task.get('experiment') and change.experiment is None and change.method_instruction:
                    raise ValueError('候选方法修订必须记录新版实验条件，不能仅替换执行指令')
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
                task['method_instruction']=change.method_instruction
                if change.experiment is not None:
                    task['experiment']=change.experiment.model_dump()
                if change.dependencies is not None:
                    task["dependencies"] = change.dependencies
                    task["subproblem"]["depends_on"] = change.dependencies
            for key, task in state["tasks"].items():
                task["assumption_ids"] = [aid for aid, assumption in state["assumptions"].items()
                                          if key in assumption["task_ids"] and assumption["status"] != "retired"]
                if key in assumption_roots and key not in ids:
                    task["revision"] += 1
            for task in state['tasks'].values():
                if task['kind']!='verify':
                    task['requirement_ids'] = [r['id'] for r in state['spec']['requirements'] if task['task_id'] in r['task_ids']]
            state['max_total_attempts'] = max(state['max_total_attempts'], len(state['tasks']) * state['max_attempts'] + 6)
            validate_dependencies(state["tasks"])
            self._bind_assumption_inputs(state)
            self._validate_contracts(state)
            affected = self._invalidate(state, set(ids) | assumption_roots, patch.reason)
            changed_assumptions = {change.assumption_id for change in patch.assumptions}
            for task in state["tasks"].values():
                if task.get("verifies_issue"):
                    issue = next(i for i in research_findings(state) if i["id"] == task["verifies_issue"])
                    if issue["assumption_id"] in changed_assumptions:
                        # A frozen countercheck of the old premise cannot refute
                        # its replacement. Fresh scope acceptance closes the repair.
                        task["retired"] = True
            for issue in research_findings(state):
                related_assumption = issue["assumption_id"] in changed_assumptions and issue["status"] not in {"resolved", "dismissed", "superseded"}
                retry_fault=issue['category']=='execution' and issue['task_id'] in affected and not state['tasks'][issue['task_id']]['retired'] and issue['status'] in {'confirmed','inconclusive'}
                if issue not in issues and not related_assumption and not retry_fault and not (patch.amendment and issue["task_id"] in affected and issue["status"] in {"confirmed", "inconclusive"}):
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
    def _bind_assumption_inputs(state):
        bound = {}
        def visit(key):
            if key not in bound:
                own = {aid for aid, a in state["assumptions"].items() if key in a["task_ids"] and a["status"] != "retired"}
                bound[key] = sorted(own | {aid for dep in state["tasks"][key]["dependencies"] for aid in visit(dep)})
            return bound[key]
        for key, task in state["tasks"].items():
            if task["kind"] != "verify":
                task["assumption_ids"] = visit(key)

    @staticmethod
    def _validate_contracts(state):
        regular = [t["subproblem"] for t in state["tasks"].values() if t["kind"] != "verify"]
        analysis = {"subproblems": regular,
            "requirements": [{k: v for k, v in r.items() if k != "revision"} for r in state["spec"]["requirements"]],
            "assumptions": [{k: v for k, v in a.items() if k in Assumption.model_fields}
                            for a in state["assumptions"].values()]}
        validate_spec(state["spec"]["requirement"] + "\n" + "\n".join(state["spec"]["amendments"]), analysis)
        for task in state["tasks"].values():
            if task["kind"] == "verify":
                continue
            for rule in protocol(state, task["task_id"]):
                right = rule["check"].get("right")
                if right and not ResearchStore._reaches(state, right["task_id"], task["task_id"]):
                    raise ValueError("跨任务验收操作数必须声明任务依赖")

    def reserve_planning_call(self, decision_key=None, *, limit=DEFAULT_PLANNING_CALL_LIMIT):
        if limit is not None and (type(limit) is not int or limit<0):
            raise ValueError('显式规划次数上限必须是非负整数')
        with self._transaction() as db:
            state = self._read(db)
            if decision_key:
                row=db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',(self.research_id,decision_key)).fetchone()
                if row and json.loads(row[0]).get('status')!='retry':
                    return False
            if limit is not None and state["planning_calls"] >= limit:
                return False
            state["planning_calls"] += 1
            if decision_key:
                db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',
                    (self.research_id,decision_key,decision_key,json.dumps({'status':'planning','base_revision':state['revision']})))
            self._save(db, state, "planning_started", {"call": state["planning_calls"],"decision":decision_key})
            return True

    def planning_decision(self, key):
        with closing(self._connect()) as db:
            row=db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',(self.research_id,key)).fetchone()
        return json.loads(row[0]) if row else None

    def save_planning_decision(self, key, record):
        with self._transaction() as db:
            state=self._read(db)
            db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',
                (self.research_id,key,key,json.dumps(record,ensure_ascii=False)))
            self._save(db,state,'research_decision',{'key':key,**record})

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

    def save_consistency(self, conflicts):
        with self._transaction() as db:
            state = self._read(db)
            if state.get('consistency') != conflicts:
                state['consistency'] = conflicts
                self._save(db, state, 'consistency_committed', {'conflicts': conflicts})

    def set_delivery_stage(self, stage, status, note):
        if stage not in {"write", "validate", "render", "export"}:
            return
        with self._transaction() as db:
            state = self._read(db)
            value = {"status": status, "note": note}
            if state.get("delivery_stages", {}).get(stage) == value:
                return
            state.setdefault("delivery_stages", {})[stage] = value
            self._save(db, state, "delivery_stage", {"stage": stage, **value})

    def load_revision_command(self, command_id, base_revision, change, scope, *, source_issue=None):
        with closing(self._connect()) as db:
            row = db.execute("SELECT digest, response FROM commands WHERE research_id=? AND id=?",
                             (self.research_id, "revision:" + command_id)).fetchone()
            if not row:
                return None
            if row[0] != digest({"base_revision": base_revision, "change": change, "scope": scope,**({'source_issue':source_issue} if source_issue else {})}):
                raise ResearchConflict("command_id 已被其他命令使用")
            return PlanPatch.model_validate_json(row[1])

    def apply_revision_command(self, command_id, base_revision, change, scope, patch, *, source_issue=None):
        from .models import is_continuation
        if not change.strip() or is_continuation(change):
            raise ValueError('继续/恢复不是研究修订，不能改变版本或作废成果')
        from .models import PlanPatch
        signature = digest({"base_revision": base_revision, "change": change, "scope": scope,**({'source_issue':source_issue} if source_issue else {})})
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
                    issue = next(i for i in research_findings(state) if i["id"] == task["verifies_issue"])
                    if issue["status"] == "inconclusive":
                        task["retired"] = False
                        issue["status"] = "suspected"
            pending = {key for key, task in state["tasks"].items()
                       if not task["retired"] and (task["acceptance"] != "accepted" or task["validity"] != "current")}
            pending |= {i["task_id"] for i in research_findings(state) if needs_owner_review(i)}
            for attempt in state["attempts"].values():
                if attempt["execution_status"] == "running":
                    attempt["execution_status"] = "cancelled"
            self._invalidate(state, pending, "从已提交业务状态恢复")
            for stage in state.get("delivery_stages", {}).values():
                if stage["status"] == "in_progress":
                    stage.update(status="pending", note="")
            for issue in research_findings(state):
                if issue["category"] == "service":
                    # Starting a retry is not proof that its audit completed.
                    # ReviewCache closes only the matching context on success.
                    continue
                elif issue["action"] == "retry" and issue["status"] == "confirmed":
                    issue["status"] = "repairing"
                    issue["repair_targets"] = {issue["task_id"]: state["tasks"][issue["task_id"]]["revision"]}
            self._save(db, state, "recovered", {"affected": sorted(pending)})

    def save_sections(self, sections: dict, issues: list, contexts: dict | None = None, *, verified=True):
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
            state["outcome"] = (("needs_attention" if issues else "completed") if self.delivery_ready(state) else "blocked") if verified else "running"
            self._save(db, state, "sections_committed", {"versions": {key: state["sections"][key] for key in sections}, "issues": issues})

    def publish_paper(self, text: str, *, filename: str = "paper.md"):
        """Keep generated versions and preserve a manually edited active paper."""
        if filename not in {"paper.md", "paper.tex"}:
            raise ValueError("不支持的论文源文件")
        with self._transaction() as db:
            state = self._read(db)
            if not self.delivery_ready(state):
                raise ResearchConflict("研究仍有阻塞问题，不能发布论文")
            suffix = Path(filename).suffix
            relative = f".system/research/{self.research_id}/papers/{state['commit_seq'] + 1}{suffix}"
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            paper = self.root / filename
            existing = paper.read_text(encoding="utf-8") if paper.exists() else None
            previous = state.get("paper_hash") if Path(state.get("paper_version", "")).suffix == suffix else None
            conflict = previous is not None and existing is not None and digest(existing) != previous
            state["paper_version"] = relative
            state["paper_conflict"] = conflict
            if conflict:
                state["outcome"] = "needs_attention"
                report = IssueReport(task_id=next(t["task_id"] for t in state["tasks"].values() if t["kind"] != "verify"),
                    category="writing", section="paper", message=f"{filename} 有人工修改，自动新稿保存在 {relative}")
                self._record_issue(state, report, "confirmed")
            else:
                temp = paper.with_suffix(suffix + ".tmp")
                temp.write_text(text, encoding="utf-8")
                temp.replace(paper)
                state["paper_hash"] = digest(text)
                state["exports"] = []
            self._save(db, state, "paper_published", {"path": relative, "content_hash": digest(text), "conflict": conflict})
            return relative if conflict else filename, conflict

    def register_exports(self, paths):
        with self._transaction() as db:
            state = self._read(db)
            state["exports"] = list(paths)
            self._save(db, state, "exports_committed", {"paths": paths, "paper_hash": state.get("paper_hash")})
