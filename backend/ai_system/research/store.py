"""SQLite is authoritative; checkpoints and results.json are projections.

Each command holds BEGIN IMMEDIATE through validation and publication. Documents
are intentionally coarse in this first schema: old revisions and attempts remain
in the journal, while reads see a single committed, consistent snapshot.
"""
from services.file_services.workspace_layout import workspace_path

from ai_system.research.domain import findings as research_findings, SCHEMA_VERSION, attach_question, save_report_artifact, execution_lineage, current_results
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager, closing
from pathlib import Path

from . import ENGINE_VERSION, DEFAULT_PLANNING_CALL_LIMIT
from .models import Assumption, DomainCheck, ResearchRevision, ResearchConflict, WorkerResult, WorkOrder, Issue, IssueReport, ValidationRecord, ContentFeedback, RuntimeFault
from .validation import bind_requirements, validate_spec, protocol, evaluate, fingerprint
from .assumptions import assumption_id, premise_definition, assumption_scope

DB_PATH = ".system/research.sqlite"


def digest(value) -> str:
    return fingerprint(value)


def validate_dependencies(tasks: dict) -> None:
    if any(not re.fullmatch(r"[QE][0-9]+[a-z]?", key) for key in tasks):
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
        self.path = workspace_path(self.root, DB_PATH)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", research_id):
            raise ValueError("研究标识格式错误")
        self.research_id = research_id
        self.read_only = read_only
        self.experience_scope = experience_scope

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
        if state and state.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("研究数据尚未迁移，请先运行 python -m services.migrate_data")
        if state:
            rows = db.execute('SELECT payload,updated_at FROM research_activity WHERE research_id=? ORDER BY started_at DESC,id DESC LIMIT 240',
                              (self.research_id,)).fetchall()
            state['activity'] = [json.loads(row[0]) for row in reversed(rows)]
            state['activity_updated_at'] = max((row[1] for row in rows), default=0)
        return state

    def record_activity(self, item):
        """Progress updates touch one row, never the business document or journal."""
        with self._transaction() as db:
            self._write_activity(db, item)

    def _write_activity(self, db, item):
        db.execute('INSERT INTO research_activity VALUES (?,?,?,?,?) ON CONFLICT(research_id,id) DO UPDATE SET updated_at=excluded.updated_at,payload=excluded.payload',
                   (self.research_id, item['id'], item['started_at'], time.time(), json.dumps(item, ensure_ascii=False)))

    def snapshot(self):
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            return self._read(db)

    def _save(self, db, state, kind, payload):
        from .runtime import execution_links
        state['updated_at']=time.time()
        payload={**execution_links(),**payload,'recorded_at':state['updated_at']}
        state["commit_seq"] += 1
        db.execute("INSERT OR REPLACE INTO research VALUES (?, ?)",
                   (self.research_id, json.dumps({k: v for k, v in state.items() if k not in {"activity", "activity_updated_at"}}, ensure_ascii=False)))
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
                "execution_id": None, "report_ref": None,
                "requirement_ids": [r.id for r in requirements if sp["id"] in r.task_ids],
                "assumption_ids": [a.id for a in assumptions if sp["id"] in a.task_ids],
                "kind": sp.get("kind", "compute"), "retired": False,
            } for sp in analysis["subproblems"]}
            validate_dependencies(tasks)
            state = {"schema_version": SCHEMA_VERSION, "engine_version": ENGINE_VERSION,
                     "research_id": self.research_id, "revision": 1, "commit_seq": 0,
                     "spec": {"requirement": requirement, "amendments": [], "output_mode": output_mode,
                              "requirements": [{**r.model_dump(), "revision": 1} for r in requirements]},
                     "assumptions": {a.id: {**a.model_dump(), "revision": 1, "status": "active", "source": "analysis", "producer_attempt": None} for a in assumptions},
                     "assumption_history": [], "validations": [],
                     "questions": {}, "experiments": tasks, "executions": {}, "artifacts": {}, "claims": {},
                     "action": None, "publication": None, "activity": [], "activity_updated_at": 0,
                     "sections": {}, "feedback": [], "faults": [], "legacy_records": [], "delivery_stages": {}, "outcome": "running", "max_attempts": max_attempts,
                     "materials": {"inputs": self.file_inputs(), "profile": ""},
                     "planning_calls": 0, "max_total_attempts": len(tasks) * max_attempts + 6}
            for experiment in tasks.values():
                attach_question(state, experiment, title=experiment["subproblem"].get("title"))
            from .migration import retire_legacy_workflow
            retire_legacy_workflow(state)
            self._validate_contracts(state)
            self._bind_assumption_inputs(state)
            self._save(db, state, "initialized", {"spec": state["spec"], "experiments": tasks})
            return state

    def allocate_ref(self, prefix: str):
        with self._transaction() as db:
            db.execute("INSERT INTO counters VALUES (?, ?, 1) ON CONFLICT(research_id, prefix) DO UPDATE SET value=value+1", (self.research_id, prefix))
            value = db.execute("SELECT value FROM counters WHERE research_id=? AND prefix=?", (self.research_id, prefix)).fetchone()[0]
            state=self._read(db) or {}
            known=set(state.get('artifacts',{})) | set(state.get('claims',{}))
            while f'{prefix}{value}' in known:
                value+=1
            db.execute('UPDATE counters SET value=? WHERE research_id=? AND prefix=?',(value,self.research_id,prefix))
            return f"{prefix}{value}"

    def _artifact_hash(self, item):
        if not item.get("path"):
            return None
        path = workspace_path(self.root, item["path"])
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
                roots = {key for key, task in state['experiments'].items() if not task['retired']}
                affected = self._invalidate(state, roots, '科研技能配置已改变，使用当前选择重新设计和求解')
                for key in affected:
                    state['experiments'][key]['revision'] += 1
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
                    path = workspace_path(self.root, path.relative_to(self.root).as_posix())
                    from services.file_services.file_helper import open_read
                    with open_read(path) as stream:
                        refs[path.relative_to(self.root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        return refs

    @staticmethod
    def _inputs(state, task, files):
        refs = dict(files) if task.get('input_files') is None else {p: files.get(p, '<missing>') for p in task['input_files']}
        refs.update({f"task:{dep}": state["experiments"][dep]["execution_id"] or ""
                     for dep in task["dependencies"]})
        refs.update({f"assumption:{aid}": str(state["assumptions"][aid]["revision"])
                     for aid in task["assumption_ids"]})
        if task.get('continuation'):refs['continuation']=task['continuation']['parent_attempt']
        return refs

    def refresh_inputs(self):
        files = self.file_inputs()
        with self._transaction() as db:
            state = self._read(db)
            changed = set()
            for key, task in state["experiments"].items():
                attempt = state["executions"].get(task["execution_id"])
                if task["acceptance"] == "accepted" and attempt:
                    previous = {k: v for k, v in attempt["input_refs"].items() if not k.startswith(("task:", "assumption:", "continuation"))}
                    current = files if task.get('input_files') is None else {p: files.get(p, '<missing>') for p in task['input_files']}
                    if previous != current:
                        changed.add(key)
            if changed:
                for key in changed:
                    state["experiments"][key]["revision"] += 1
                affected = self._invalidate(state, changed, "原始数据文件已更正")
                state["revision"] += 1
                self._save(db, state, "inputs_changed", {"affected": affected})
            return state

    def claim(self, task_id: str) -> WorkOrder:
        files = self.file_inputs()
        with self._transaction() as db:
            state = self._read(db)
            task = state["experiments"][task_id]
            if task["retired"] or task["execution_status"] != "pending":
                raise ResearchConflict(f"任务 {task_id} 已领取或需要显式重试")
            if any(state["experiments"][dep]["acceptance"] != "accepted" or
                   state["experiments"][dep]["validity"] != "current" for dep in task["dependencies"]):
                raise ResearchConflict(f"任务 {task_id} 的依赖尚未验收")
            if any(state["assumptions"][aid]["status"] == "refuted" for aid in task["assumption_ids"]):
                raise ResearchConflict("任务依赖的假设已被反证")
            if any(i["blocking"] and i["status"] == "confirmed" and task_id in i["repair_targets"] for i in research_findings(state)):
                raise ResearchConflict("任务需要先提交修复计划")
            from .operations import attempt_budget_error
            if error := attempt_budget_error(state, task):
                raise ResearchConflict(error)
            refs = self._inputs(state, task, files)
            order = WorkOrder(attempt_id=uuid.uuid4().hex, task_id=task_id, revision=task["revision"],
                              input_digest=digest(refs), input_refs=refs, lease=uuid.uuid4().hex,
                              protocol_hash=digest(protocol(state, task_id)))
            from .experience import conditions
            basis = {'question':task['objective'],'protocol':protocol(state,task_id),
                     'requirements':[r for r in state['spec']['requirements'] if r['id'] in task['requirement_ids']],
                     'conditions':conditions(self.root,state['spec']['requirement']+' '+task['objective'])}
            state["executions"][order.attempt_id] = {**order.model_dump(), "execution_status": "running", "started_at": time.time(),
                                                 'experience_basis':basis}
            if task.get('continuation'):
                state['executions'][order.attempt_id].update(task['continuation'])
            if task.get('experiment'):
                state['executions'][order.attempt_id]['experiment']=task['experiment']
            task.update(execution_status="running", execution_id=order.attempt_id)
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
            state=self._read(db);attempt=state['executions'][order.attempt_id]
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
            attempt = state["executions"][result.order.attempt_id]
            if attempt.get("commit"):
                if attempt["result_digest"] != signature:
                    raise ResearchConflict("同一尝试不能提交不同结果")
                return attempt["commit"]
            order = result.order
            task = state["experiments"][order.task_id]
            if any(attempt[key] != getattr(order, key) for key in ("task_id", "revision", "lease", "input_digest", "input_refs", "protocol_hash", "review_required", "review_issue_ids")):
                raise ResearchConflict("执行权或冻结输入不匹配")
            if attempt.get('experience_refs',[])!=order.experience_refs:
                raise ResearchConflict('冻结经验引用不匹配')
            experience_current = not order.experience_refs or (self.experience_scope is not None and self.experience_scope.valid_refs(order.experience_refs))
            current = (task["revision"] == order.revision and task["execution_id"] == order.attempt_id
                       and task["validity"] == "current" and attempt["execution_status"] == "running"
                       and self._inputs(state, task, files) == order.input_refs
                       and digest(protocol(state, order.task_id, execution_id=order.attempt_id)) == order.protocol_hash and experience_current)
            receipt = {"attempt_id": order.attempt_id, "published": current, "commit_seq": state["commit_seq"] + 1}
            if not experience_current:
                receipt['reason']='引用经验已禁用、删除、变更或来源/权限失效，需重新领取任务'
            attempt.update(execution_status=result.execution_status, acceptance=result.acceptance,
                           result_digest=signature, commit=receipt, result=result.model_dump())
            if current and attempt.get('parent_attempt') and result.execution_status != 'succeeded':
                # A failed additional action does not retract previously valid
                # evidence or interpretation. Retain this failed attempt separately.
                parent=attempt['parent_attempt']
                for item in result.results:
                    if item['id'] in state['artifacts']:continue
                    if item['subproblem']!=order.task_id:raise ValueError('补充结果不属于当前实验')
                    state['artifacts'][item['id']]={'payload':item,'content_hash':digest(item),
                        'artifact_hash':self._artifact_hash(item),'producer_attempt':order.attempt_id,
                        'experiment_id':order.task_id,'type':'result','active':False}
                historical={**task,'execution_id':order.attempt_id,'report_ref':None}
                save_report_artifact(state,self.root,historical,result.report)
                state['artifacts'][historical['report_ref']]['active']=False
                task.update(execution_id=parent,execution_status='succeeded',acceptance='accepted')
                task.pop('continuation',None)
                for report in result.issues:
                    self._record_issue(state,IssueReport(task_id=order.task_id,category='execution',
                        message=report.message,evidence_refs=report.evidence_refs),'confirmed',producer_attempt=order.attempt_id)
                receipt.update(reason='补充行动失败；原实验结果与解释仍保留为当前依据',continued=False)
                from .domain import register_execution_artifacts
                register_execution_artifacts(state,self.root,attempt)
                self._save(db,state,'attempt_committed',receipt)
                return receipt
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
                inherited = execution_lineage(state, attempt.get('parent_attempt'))
                for item in all_evidence.values():
                    previous=state['artifacts'].get(item['id'])
                    if item['subproblem'] != order.task_id or (previous and
                        (not previous['active'] or previous['producer_attempt'] not in inherited or previous['payload'] != item)):
                        raise ValueError('结果编号重复或不属于当前任务/延续来源')
                ids = {r["id"] for r in result.results}
                frozen = set().union(*(execution_lineage(state,v) for k,v in order.input_refs.items() if k.startswith("task:")))
                input_evidence = {rid for rid, entry in state["artifacts"].items() if entry["producer_attempt"] in frozen and entry["active"]}
                for claim in result.claims:
                    if claim["id"] in state["claims"] or claim["subproblem"] != order.task_id:
                        raise ValueError("结论编号重复或不属于当前任务")
                    if not set(claim.get("evidence", [])) <= (ids | input_evidence):
                        raise ValueError("结论引用了不可用证据")
                for item in all_evidence.values():
                    if item["id"] in state["artifacts"]:continue  # Reuse original provenance unchanged.
                    state["artifacts"][item["id"]] = {"payload": item, "content_hash": digest(item),
                        "artifact_hash": self._artifact_hash(item), "producer_attempt": order.attempt_id,
                        "experiment_id": order.task_id, "type": "result", "active": item["id"] in ids}
                # Continuations retain upstream results. current_results selects
                # newer versions by declared identity, without discarding other scopes.
                introduced = self._register_assumptions(state, result) if result.execution_status == "succeeded" else []
                for old in state['claims'].values():
                    if old['experiment_id']==order.task_id and old.get('active',True):
                        old.update(active=False,status='superseded',reason='实验继续行动后更新解释')
                for item in result.claims:
                    state["claims"][item["id"]] = {"payload": item, "producer_attempt": order.attempt_id, "experiment_id":order.task_id,
                        "active": True, "status": "current", "assumption_refs": {k.removeprefix("assumption:"): int(v) for k, v in order.input_refs.items() if k.startswith("assumption:")}}
                review_clear = self._commit_reviews(state, result, set(all_evidence) | input_evidence)
                if introduced:
                    attempt['introduced_assumptions'] = introduced
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
                            validity="current")
                save_report_artifact(state,self.root,task,result.report)
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
                        task.update(acceptance="accepted", validity="current", execution_id=order.attempt_id,
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

            else:
                attempt["validity"] = "stale"
                if task["execution_id"] == order.attempt_id:
                    task['execution_status']=result.execution_status
                    task["revision"] += 1
                    state["revision"] += 1
                    self._invalidate(state, {order.task_id}, "执行期间输入已改变")
            if receipt['published'] and order.experience_refs and not self.experience_scope.valid_refs(order.experience_refs):
                raise ResearchConflict('提交前经验已失效，保留先前结果并重新领取任务')
            from .domain import register_execution_artifacts
            register_execution_artifacts(state, self.root, attempt)
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
            state=self._read(db);task=state['experiments'][order.task_id]
            if task['execution_id']!=order.attempt_id or task['revision']!=order.revision:return
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
        if state["experiments"][root]["kind"] != "compute":
            raise ValueError("验证任务不能引入新的建模前提")
        scope = assumption_scope(state["experiments"], root)
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
        # These premises were declared by this execution, not discovered by a
        # separate scheduling pass. Preserve that provenance without recomputing
        # an unchanged experiment solely to attach newly allocated identifiers.
        for key in scope:
            task = state['experiments'][key]
            task['assumption_ids'] += [aid for aid in introduced if aid not in task['assumption_ids']]
        return introduced

    def _commit_reviews(self, state, result, available):
        # Historical worker reviews are advisory input. Only deterministic
        # contracts (evaluate) or an explicit owner decision can invalidate work.
        for review in result.reviews:
            for report in review.issues:
                if not set(report.evidence_refs) <= available:
                    raise ValueError('核查引用了冻结上下文之外的证据')
                issue = self._record_issue(state, report, 'suspected',
                    producer_attempt=result.order.attempt_id, source='local_review')
                issue['blocking'] = False
        return result.execution_status == 'succeeded'

    @staticmethod
    def _record_issue(state, report, status, producer_attempt=None, source="worker", target_revision=None):
        if report.task_id not in state["experiments"]:
            raise ValueError("问题指向未知任务")
        task = state["experiments"][report.task_id]
        known = set(state["artifacts"])
        if not set(report.evidence_refs) <= known:
            raise ValueError("问题引用了未知证据")
        if report.assumption_id and report.assumption_id not in state["assumptions"]:
            raise ValueError("问题指向未知假设")
        revision = target_revision if target_revision is not None else task["revision"]
        identity = digest({**report.model_dump(), "revision": revision, "source": source})[:24]
        advisory = source in {'local_review','semantic_check','reconcile','sections'} or source.startswith(('review:','review-task:'))
        previous = next((i for i in research_findings(state) if i["id"] == identity), None)
        if previous:
            if (report.category in {"writing", "service"} or report.category=='coverage' and source=='sections' or source == "local_review" and previous["producer_attempt"] != producer_attempt) and previous["status"] in {"resolved", "dismissed", "superseded"}:
                previous["status"] = status
                previous['blocking']=not advisory and (report.category!='writing' or report.section!='paper')
            if advisory:previous['blocking']=False
            return previous
        record_type = RuntimeFault if report.category in {'execution', 'service'} else ContentFeedback
        issue = record_type(**report.model_dump(), id=identity, task_revision=revision,
            producer_attempt=producer_attempt, status=status, source=source,
            assumption_revision=state["assumptions"][report.assumption_id]["revision"] if report.assumption_id else None,
            action="retry" if report.category in {"execution", "service"} else "rewrite" if report.category == "writing" else "verify",
            blocking=not advisory and (report.category != "writing" or report.section != "paper")).model_dump()
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
        issue["repair_targets"] = {key: state["experiments"][key]["revision"] for key in affected if not state["experiments"][key]["retired"]}
        issue["action"] = "recompute"

    @staticmethod
    def _resolve_repairs(state, resolution_attempt):
        for issue in research_findings(state):
            if issue["status"] != "repairing":
                continue
            if issue["source"] == "local_review" and issue["action"] != "recompute":
                continue  # Only a fresh review or an independent check closes these findings.
            if all(state["experiments"][key]["acceptance"] == "accepted" and
                   state["experiments"][key]["validity"] == "current" and state["experiments"][key]["revision"] >= revision
                   for key, revision in issue["repair_targets"].items()):
                issue["status"] = "resolved"
                issue["resolution_attempt"] = resolution_attempt

    def report_issues(self, reports, source, *, close_writing=True):
        with self._transaction() as db:
            state = self._read(db)
            if source == "sections" and close_writing:
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
        from .questions import delivery_status
        return delivery_status(state)['ready']

    @staticmethod
    def _active_registry(state):
        accepted = set().union(*(execution_lineage(state,t['execution_id']) for t in state['experiments'].values()
                    if t['kind'] != 'verify' and not t['retired'] and t['acceptance'] == 'accepted' and t['validity'] == 'current'))
        results = current_results(state)
        claims = [c["payload"] for c in state["claims"].values() if c["producer_attempt"] in accepted and c.get("active", True)]
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
            more = {key for key, task in state["experiments"].items() if set(task["dependencies"]) & affected}
            if more <= affected:
                break
            affected |= more
        for key in affected:
            task = state["experiments"][key]
            if task["retired"]:
                continue
            task.update(validity='stale', reason=reason)
            if task['execution_id']:
                state['executions'][task['execution_id']]['validity']='stale'
            elif task['execution_status']=='pending':
                task['retired']=True
        for qid in {state['experiments'][key]['question_id'] for key in affected}:
            state['questions'][qid].update(judgment='',artifact_refs=[],next_step=reason)
        state['publication']=None
        for key, section in state["sections"].items():
            if not key.startswith("solve:") or set(section["task_refs"]) & affected:
                section["validity"] = "stale"
        state["outcome"] = "running"
        return sorted(affected)

    @staticmethod
    def _reaches(state, upstream, target):
        if upstream == target:
            return True
        return any(ResearchStore._reaches(state, upstream, dep) for dep in state["experiments"][target]["dependencies"])

    @staticmethod
    def _bind_assumption_inputs(state):
        bound = {}
        def visit(key):
            if key not in bound:
                own = {aid for aid, a in state["assumptions"].items() if key in a["task_ids"] and a["status"] != "retired"}
                bound[key] = sorted(own | {aid for dep in state["experiments"][key]["dependencies"] for aid in visit(dep)})
            return bound[key]
        for key, task in state["experiments"].items():
            if task["kind"] != "verify":
                task["assumption_ids"] = visit(key)

    @staticmethod
    def _validate_contracts(state):
        regular = [t["subproblem"] for t in state["experiments"].values() if t["kind"] != "verify"]
        analysis = {"subproblems": regular,
            "requirements": [{k: v for k, v in r.items() if k != "revision"} for r in state["spec"]["requirements"]],
            "assumptions": [{k: v for k, v in a.items() if k in Assumption.model_fields}
                            for a in state["assumptions"].values()]}
        validate_spec(state["spec"]["requirement"] + "\n" + "\n".join(state["spec"]["amendments"]), analysis)
        for task in state["experiments"].values():
            if task["kind"] == "verify":
                continue
            for rule in protocol(state, task["task_id"]):
                right = rule["check"].get("right")
                if right and not ResearchStore._reaches(state, right["task_id"], task["task_id"]):
                    raise ValueError("跨任务验收操作数必须声明任务依赖")

    def reserve_planning_call(self, *, limit=DEFAULT_PLANNING_CALL_LIMIT):
        with self._transaction() as db:
            state=self._read(db)
            if limit is not None and state['planning_calls']>=limit:return False
            state['planning_calls']+=1
            self._save(db,state,'observation_started',{'call':state['planning_calls']})
            return True

    def set_outcome(self, outcome):
        with self._transaction() as db:
            state = self._read(db)
            if state["outcome"] != outcome:
                state["outcome"] = outcome
                self._save(db, state, "outcome_changed", {"outcome": outcome})

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
            return ResearchRevision.model_validate(json.loads(row[1])['command'])

    def apply_revision_command(self, command_id, base_revision, change, scope, patch, *, source_issue=None):
        # Revision semantics are selected at the caller. This boundary verifies
        # the structured contract, never guesses intent from the change text.
        patch = ResearchRevision.model_validate(patch)
        if not change.strip() or (patch.command_id, patch.base_revision, patch.change) != (command_id, base_revision, change):
            raise ValueError('修订契约与命令不一致')
        selected = set(patch.experiment_ids)
        if (len(selected) != len(patch.experiment_ids) or
                scope is not None and (len(set(scope)) != len(scope) or selected != set(scope)) or
                {key for r in patch.requirements for key in r.task_ids} != selected):
            raise ValueError('修订要求与修改范围不一致')
        signature = digest({"base_revision": base_revision, "change": change, "scope": scope,**({'source_issue':source_issue} if source_issue else {})})
        key = 'revision:' + command_id
        with self._transaction() as db:
            row=db.execute('SELECT digest,response FROM commands WHERE research_id=? AND id=?',
                           (self.research_id,key)).fetchone()
            if row:
                if row[0]!=signature:raise ResearchConflict('command_id 已被其他修改使用')
                return json.loads(row[1])
            state=self._read(db)
            if state['revision']!=base_revision:raise ResearchConflict('研究版本已改变，请刷新后重试')
            if not selected<=state['experiments'].keys():raise ValueError('修改引用了未知实验')
            source='\n'.join([state['spec']['requirement'],*state['spec']['amendments'],change])
            validate_spec(source, {'subproblems': [{'id': key} for key in selected],
                                  'requirements': [r.model_dump() for r in patch.requirements], 'assumptions': []})
            # Revise question scope, not the inputs or conditions of old experiments.
            state['spec']['requirements']=bind_requirements(state,patch.requirements)
            state['spec']['amendments'].append(change)
            from .questions import apply_questions
            from .questions import QuestionUpdate
            affected=sorted(selected)
            for qid in dict.fromkeys(state['experiments'][eid]['question_id'] for eid in affected):
                question=state['questions'][qid]
                apply_questions(state,[QuestionUpdate(question_id=qid,base_version=question.get('version',1),
                    question=question['question']+'\n补充要求：'+change,reason=change,
                    experiment_ids=[eid for eid in affected if state['experiments'][eid]['question_id']==qid])])
                question['next_step']='评估已有证据的适用范围；新方法或条件按需安排实验'
            state['revision']+=1
            state['action']=None;state['publication']=None
            state['outcome']='running'
            receipt={'revision':state['revision'],'affected':affected,'command':patch.model_dump()}
            db.execute('INSERT INTO commands VALUES (?,?,?,?)',
                       (self.research_id,key,signature,json.dumps(receipt,ensure_ascii=False)))
            self._save(db,state,'research_revised',receipt)
            return receipt

    def recover(self):
        """Called only by the single work coordinator after its previous run ended.

        An interrupted kernel cannot be restored from a graph checkpoint. Failed
        attempts remain in history; retries acquire new leases and attempt IDs.
        """
        with self._transaction() as db:
            state = self._read(db)
            if not state:
                return
            for task in state["experiments"].values():
                if task.get("verifies_issue"):
                    issue = next(i for i in research_findings(state) if i["id"] == task["verifies_issue"])
                    if issue["status"] == "inconclusive":
                        task["retired"] = False
                        issue["status"] = "suspected"
            for operation in state.get('activity',[]):
                if operation['status']=='running':
                    operation['status']='interrupted'
                    operation['interruption_detected_at']=time.time()
                    self._write_activity(db, operation)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='model_requests'").fetchone():
                for row in db.execute('SELECT research_id,sequence,payload FROM model_requests').fetchall():
                    request=json.loads(row['payload'])
                    if request.get('research_id')==self.research_id and request.get('state') in {'sent','http_response'}:
                        request['state']='recorded' if request.get('usage_recorded') else 'interrupted'
                        request['interruption_detected_at']=time.time()
                        db.execute('UPDATE model_requests SET payload=? WHERE research_id=? AND sequence=?',
                            (json.dumps(request),row['research_id'],row['sequence']))
            pending = set()
            for execution in state['executions'].values():
                if execution['execution_status'] == 'running':
                    execution['execution_status'] = 'cancelled'
                    experiment = state['experiments'][execution['task_id']]
                    if experiment['execution_id'] == execution['attempt_id']:
                        if execution.get('parent_attempt'):
                            experiment.update(execution_id=execution['parent_attempt'],execution_status='succeeded',acceptance='accepted')
                            experiment.pop('continuation',None)
                            continue
                        experiment.update(execution_status='cancelled', acceptance='inconclusive')
                        pending.add(execution['task_id'])
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
                    issue["repair_targets"] = {issue["task_id"]: state["experiments"][issue["task_id"]]["revision"]}
            self._save(db, state, "recovered", {"affected": sorted(pending)})

    def save_sections(self, sections: dict, issues: list, contexts: dict | None = None, *, verified=True):
        with self._transaction() as db:
            state = self._read(db)
            changed = {}
            for key, text in sections.items():
                old = state["sections"].get(key)
                context_hash = (contexts or {}).get(key)
                if old and old["text"] == text and old["validity"] == "current" and old.get("context_hash") == context_hash:
                    continue
                refs = [key.split(":", 1)[1]] if key.startswith("solve:") else list(state["experiments"])
                state["sections"][key] = {"revision": (old or {}).get("revision", 0) + 1,
                    "text": text, "content_hash": digest(text), "context_hash": context_hash,
                    "evidence_refs": re.findall(r"\{\{[RF]:([RF][0-9]+)\}\}", text),
                    "task_refs": refs, "validity": "current"}
                changed[key] = state['sections'][key]
            if not changed:
                return
            state["outcome"] = "running"
            self._save(db, state, "sections_committed", {"versions": changed, "issues": issues})

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
            target = workspace_path(self.root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            from services.file_services.file_helper import atomic_write
            atomic_write(target, lambda f: f.write(text.encode("utf-8")))
            paper = self.root / filename
            existing = paper.read_text(encoding="utf-8") if paper.exists() else None
            previous = state.get("paper_hash") if Path(state.get("paper_version", "")).suffix == suffix else None
            conflict = previous is not None and existing is not None and digest(existing) != previous
            state["paper_version"] = relative
            state["paper_conflict"] = conflict
            if conflict:
                state["outcome"] = "needs_attention"
                report = IssueReport(task_id=next(t["task_id"] for t in state["experiments"].values() if t["kind"] != "verify"),
                    category="writing", section="paper", message=f"{filename} 有人工修改，自动新稿保存在 {relative}")
                self._record_issue(state, report, "confirmed")
            else:
                atomic_write(paper, lambda f: f.write(text.encode('utf-8')))
                state["paper_hash"] = digest(text)
                state["exports"] = []
            self._save(db, state, "paper_published", {"path": relative, "content_hash": digest(text), "conflict": conflict})
            return relative if conflict else filename, conflict

    def register_exports(self, paths):
        with self._transaction() as db:
            state = self._read(db)
            state["exports"] = list(paths)
            self._save(db, state, "exports_committed", {"paths": paths, "paper_hash": state.get("paper_hash")})
