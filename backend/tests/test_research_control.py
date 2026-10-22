"""Perturbation tests for the authoritative research state and publication rules."""

from ai_system.research.domain import findings as research_findings
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.research import ENGINE_VERSION
from ai_system.research.models import WorkerResult, ResearchConflict, IssueReport, Requirement
from ai_system.research.store import ResearchStore
from ai_system.research.planner import ready_tasks


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


def test_continued_worker_can_read_published_figure_at_registered_path(tmp_path):
    from types import SimpleNamespace
    from ai_system.graph.context import GraphContext
    from ai_system.graph.registry import Result
    from ai_system.research.control import worker_context
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    path = 'outputs/research/previous/F4_F4.png'
    ctx.fs.write_text(path, 'published image')
    figure = Result('F4', 'figure', 'E1', '图', '', 'old-run', path=path)
    ctx.registry.results['F4'] = figure
    ctx.research = SimpleNamespace(
        snapshot=lambda: {'experiments': {'E1': {'dependencies': []}},
                          'executions': {'next': {'parent_attempt': 'previous'}}},
        allocate_ref=lambda kind: kind + '5', file_inputs=lambda: {})
    local = worker_context(ctx, SimpleNamespace(task_id='E1', attempt_id='next', revision=1, input_refs={}))
    assert local.fs.read_text(local.registry.results['F4'].path) == 'published image'
    assert local.registry.results['F4'].run_id == 'old-run'
    assert ctx.fs.read_text(path) == 'published image'


def finish(store, key, *, acceptance="accepted", order=None):
    order = order or store.claim(key)
    ref = store.allocate_ref("R")
    result = WorkerResult(order=order, execution_status="succeeded", acceptance=acceptance,
        report={"status": "completed", "summary": key, "requirement_evidence":
            [{"requirement_id": rid, "evidence_refs": [ref]} for rid in store.snapshot()["experiments"][key]["requirement_ids"]]},
        results=[{"id": ref, "subproblem": key, "kind": "number", "name": "value", "value": "42", "run_id": "run_test"}],
        issues=[IssueReport(task_id=key, category="execution", message="temporary execution failure")] if acceptance == "rejected" else [])
    return result, store.commit(result)


def test_repeat_commit_is_idempotent_and_mismatched_retry_rejected(tmp_path):
    store = make_store(tmp_path)
    result, receipt = finish(store, "Q1")
    seq = store.snapshot()["commit_seq"]
    assert store.commit(result) == receipt
    assert store.snapshot()["commit_seq"] == seq
    with pytest.raises(ResearchConflict):
        store.commit(result.model_copy(update={"report": {"different": True}}))
    assert len(store.registry_payload()["results"]) == 1








def test_rejected_execution_never_unblocks_dependents(tmp_path):
    store = make_store(tmp_path)
    finish(store, "Q1", acceptance="rejected")
    snapshot = store.snapshot()
    assert snapshot["experiments"]["Q1"]["execution_status"] == "succeeded"
    assert snapshot["experiments"]["Q1"]["acceptance"] == "rejected"
    assert "Q2" not in ready_tasks(snapshot)
    assert store.registry_payload()["results"] == []
    with pytest.raises(ResearchConflict):
        store.claim("Q2")


def test_file_correction_during_execution_rejects_result(tmp_path):
    store = make_store(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "input.csv").write_text("old")
    order = store.claim("Q1")
    (data / "input.csv").write_text("corrected")
    result,receipt=finish(store, "Q1", order=order)
    assert not receipt['published']
    state=store.snapshot();task=state['experiments']['Q1'];attempt=state['executions'][order.attempt_id]
    assert task['execution_status']==attempt['execution_status']=='succeeded'
    assert task['validity']==attempt['validity']=='stale' and task['revision']==2
    assert attempt['result']['results']==result.results
    assert not store.registry_payload()['results'] and not store.delivery_ready(state)
    with pytest.raises(ResearchConflict,match='显式重试'):store.claim('Q1')




def test_recovery_uses_commits_and_reclaims_interrupted_attempt(tmp_path):
    store = make_store(tmp_path)
    finish(store, "Q1")
    abandoned = store.claim("Q2")
    before = store.registry_payload()
    recovered = ResearchStore(tmp_path, "research")
    recovered.recover()
    assert recovered.registry_payload() == before
    from ai_system.research.operations import continue_experiment
    from types import SimpleNamespace
    continue_experiment(SimpleNamespace(research=recovered),{'experiment_id':'Q2','information_need':'显式恢复中断的执行，不重跑已接受的上游结果'})
    replacement = recovered.claim("Q2")
    assert replacement.attempt_id != abandoned.attempt_id
    assert replacement.revision == abandoned.revision
    replacement_task=recovered.snapshot()['experiments']['Q2']
    late,receipt=finish(recovered, "Q2", order=abandoned)
    assert not receipt['published']
    assert recovered.snapshot()['experiments']['Q2']==replacement_task
    assert replacement_task['execution_status']=='running' and replacement_task['execution_id']==replacement.attempt_id
    assert finish(recovered, "Q2", order=replacement)[1]["published"]
    completed=recovered.snapshot()
    assert recovered.commit(late)==receipt
    assert recovered.snapshot()==completed


def test_retry_budget_cannot_be_bypassed_by_recovery(tmp_path):
    store = make_store(tmp_path)
    from research_tools_helpers import continue_experiment
    for index in range(3):
        if index:continue_experiment(store,"Q1")
        finish(store, "Q1", acceptance="rejected")
        store.recover()
    assert "Q1" not in ready_tasks(store.snapshot())
    before = store.snapshot()
    with pytest.raises(ResearchConflict, match="预算"):
        continue_experiment(store,"Q1")
    assert store.snapshot() == before


def test_data_correction_after_commit_invalidates_all_coarse_file_consumers(tmp_path):
    store = make_store(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "input.csv").write_text("old")
    finish(store, "Q1")
    finish(store, "Q2")
    (data / "input.csv").write_text("corrected")
    before=store.snapshot()
    after=store.refresh_inputs()
    assert all(after['experiments'][key]['validity']=='stale' and after['experiments'][key]['acceptance']=='accepted' for key in ('Q1','Q2'))
    assert after['artifacts']==before['artifacts']
    for aid,execution in before['executions'].items():
        assert after['executions'][aid]['result']==execution['result']
        assert after['executions'][aid]['input_refs']==execution['input_refs']
    assert not store.registry_payload()['results']
    from research_tools_helpers import add_experiment,continue_experiment
    with pytest.raises(ValueError,match='失效'):continue_experiment(store,'Q1')
    eid=add_experiment(store,question_id=after['experiments']['Q1']['question_id'],
        method='读取已更正数据，重新计算本问题结果',conditions='已更正的 input.csv 版本',evaluation='原问题下保留实际输出')
    order=store.claim(eid)
    assert eid not in before['experiments'] and order.input_refs['data/input.csv']==store.file_inputs()['data/input.csv']
    assert order.input_refs['data/input.csv']!=next(iter(before['executions'].values()))['input_refs']['data/input.csv']
    finish(store,eid,order=order)
    assert {r['subproblem'] for r in store.registry_payload()['results']}=={eid}
    assert store.snapshot()['artifacts'].keys() >= before['artifacts'].keys()


def test_manual_edit_preserves_both_paper_versions(tmp_path):
    store = make_store(tmp_path)
    for key in ("Q1", "Q2", "Q3"):
        finish(store, key)
    from research_tools_helpers import answer
    current=store.snapshot();results=store.registry_payload()['results']
    for key in ('Q1','Q2','Q3'):
        answer(store,coverage='answered',stop_reason='已回答本次给定样本范围，推广证据仍未知',question_id=current['experiments'][key]['question_id'],
            statement='本次条件下的结果 {{R:'+next(r['id'] for r in results if r['subproblem']==key)+'}}',
            artifact_refs=[r['id'] for r in results if r['subproblem']==key],limitations=['仅适用于当前实验条件'])
    assert store.publish_paper("generated one") == ("paper.md", False)
    (tmp_path / "paper.md").write_text("human edits")
    path, conflict = store.publish_paper("generated two")
    assert conflict and (tmp_path / path).read_text() == "generated two"
    assert (tmp_path / "paper.md").read_text() == "human edits"
    assert store.snapshot()["outcome"] == "needs_attention"




def test_snapshot_is_read_only_and_uses_authoritative_database(tmp_path):
    from services.chat_services.research_session import research_snapshot
    store = make_store(tmp_path)
    finish(store, "Q1")
    thread = tmp_path / ".system" / "graph_thread.json"
    thread.write_text(json.dumps({"engine_version": ENGINE_VERSION, "thread_id": "research", "status": "blocked"}))
    (thread.parent / "results.json").write_text('{broken projection')
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    snapshot = research_snapshot(tmp_path, "work", "paused")
    assert len(snapshot.results) == 1 and snapshot.experiments
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_single_claim_wins_when_schedulers_race(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = make_store(tmp_path)

    def claim():
        try:
            return store.claim("Q1")
        except ResearchConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        orders = list(pool.map(lambda _: claim(), range(2)))
    assert sum(order is not None for order in orders) == 1


def test_duplicate_revision_command_reuses_original_objective(tmp_path):
    from ai_system.research.planner import propose_revision
    store = make_store(tmp_path)
    command = dict(command_id="human", base_revision=1, change="use corrected assumption", scope=["Q1"])
    requirements = [Requirement(id="RQ4", statement=command["change"], source_quote=command["change"], kind="goal", task_ids=["Q1"])]
    patch = propose_revision(store.snapshot(), **command, requirements=requirements)
    before=store.snapshot()
    first = store.apply_revision_command(**command, patch=patch)
    revised=store.snapshot()
    retried = propose_revision(store.snapshot(), **command, requirements=requirements)
    assert store.apply_revision_command(**command, patch=retried) == first
    assert store.snapshot()==revised
    assert revised['experiments']==before['experiments']
    question=revised['questions'][revised['experiments']['Q1']['question_id']]
    assert question['question'].count(command['change'])==1 and question['version']==2
    assert len(question['history'])==1






def test_staged_value_change_gets_new_reference_without_rebinding_old_reference(tmp_path):
    from ai_system.graph.registry import ResultRegistry
    from services.file_services.workspace_fs import WorkspaceFS
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run = fs.create_run()
    fs.save_run_stdout(run, "old 1.25 new 2.75")
    fs.finish_run(run, "success")
    registry = ResultRegistry(fs)
    first = registry.record_value("Q1", "estimate", "1.25", run)
    second = registry.record_value("Q1", "estimate", "2.75", run)
    assert first.id != second.id
    assert registry.get(first.id) is None
    assert registry.staged_history[0].value == "1.25"
    assert registry.for_subproblem("Q1") == [second]


def test_section_history_retains_previous_text_and_context(tmp_path):
    import sqlite3
    store = make_store(tmp_path)
    store.save_sections({"solve:Q1": "original"}, [], {"solve:Q1": "context-one"})
    store.save_sections({"solve:Q1": "revised"}, [], {"solve:Q1": "context-two"})
    with sqlite3.connect(store.path) as db:
        entries = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM journal WHERE kind='sections_committed' ORDER BY seq")]
    assert entries[0]["versions"]["solve:Q1"]["text"] == "original"
    assert entries[0]["versions"]["solve:Q1"]["context_hash"] == "context-one"
    assert entries[1]["versions"]["solve:Q1"]["revision"] == 2


def test_writer_outline_is_committed_and_reused_after_resume(tmp_path,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from ai_system.graph.context import GraphContext
    from ai_system.research import loop
    from ai_system.research.control import graph_view
    from ai_system.research.operations import deliver
    from test_native_latex import accepted_store
    store=accepted_store(tmp_path);ctx=GraphContext.create(str(tmp_path),planner_llm=None);ctx.research=store
    before=store.snapshot()['executions']
    outline=[{'key':'abstract','title':'摘要'},{'key':'custom:recommendations','title':'结论与建议'}]
    deliver(ctx,{'title':'研究交付','reason':'当前问题已有带依据的答案'})
    async def write(_ctx,name,**inputs):
        assert name=='writing.write'
        return {'sections':{'abstract':'已有证据与局限。'},'outline':outline,'section_contexts':{}}
    monkeypatch.setattr(loop,'invoke',write)
    action=store.snapshot()['action']
    asyncio.run(loop.act({'action_id':action['id']},SimpleNamespace(context=ctx)))
    assert store.snapshot()['publication']['outline']==outline
    assert store.snapshot()['executions']==before
    loop.resume(store)
    assert not asyncio.run(loop.prepare_action(ctx))
    assert store.snapshot()['action']['capability']=='writing.validate'
    assert graph_view(store.snapshot())['outline']==outline
