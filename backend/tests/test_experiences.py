"""Cross-study advice with real SQLite/checks, real kernel evidence, no model calls."""
import json
import pytest

from ai_system.graph.context import GraphContext
from ai_system.research.experience import conditions, prompt_context
from helpers.experience import TASK, analysis, env, submit, adopt_ready
from services.file_services.workspace_layout import workspace_path


def test_missing_or_changed_run_logs_stop_future_injection(env):
    a=env.store();_,r=submit(a);adopt_ready(env.scope());actual=conditions(a.root,TASK)
    assert env.scope('B').search(TASK,['linear_programming'],actual)
    manifest=json.loads((workspace_path(a.root, 'manifest.json')).read_text())
    run=manifest['runs'][r.results[0]['run_id']]
    workspace_path(a.root, run['stdout']).unlink()
    assert not env.scope('B').search(TASK,['linear_programming'],actual)
    env.scope().rebuild()
    assert not env.scope('B').search(TASK,['linear_programming'],actual)


def test_changed_stdout_cannot_be_revalidated_by_rebuilding_index(env):
    a=env.store();_,r=submit(a);adopt_ready(env.scope());actual=conditions(a.root,TASK)
    assert env.scope('B').search(TASK,['linear_programming'],actual)
    manifest=json.loads((workspace_path(a.root, 'manifest.json')).read_text())
    workspace_path(a.root, manifest['runs'][r.results[0]['run_id']]['stdout']).write_text('2 10\nchanged after commit')
    assert not env.scope('B').search(TASK,['linear_programming'],actual)
    env.scope().rebuild()
    assert not env.scope('B').search(TASK,['linear_programming'],actual)
    assert env.scope().list()['cards'][0]['card']['level']=='unconfirmed'


def test_revocation_during_commit_rolls_back_before_publication(env,monkeypatch):
    from ai_system.research.models import ResearchConflict
    a=env.store();submit(a);adopt_ready(env.scope());b=env.store('B');order=b.claim('Q1');scope=b.experience_scope
    ctx=GraphContext.create(str(b.root),planner_llm=None,experience_scope=scope,experience_store=b,experience_order=order)
    prompt_context(ctx,{'source':TASK,'subproblem':analysis()['subproblems'][0]},['linear_programming'],'solve')
    decisions=iter([True,False]);monkeypatch.setattr(scope,'valid_refs',lambda _:next(decisions))
    with pytest.raises(ResearchConflict,match='提交前经验已失效'):submit(b,order=order)
    assert not b.registry_payload()['results']
    assert 'commit' not in b.snapshot()['executions'][order.attempt_id]
