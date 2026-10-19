"""Frozen linear constraints reject fabricated feasibility, and use the existing repair lifecycle."""

from ai_system.research.domain import findings as research_findings
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.research import ENGINE_VERSION
from ai_system.research.models import DomainCheck, LinearModel, PlanPatch, TaskChange
from ai_system.research.store import ResearchStore
from ai_system.research.planner import ready_tasks
from services.chat_services.research_session import research_snapshot
from test_research_p2 import submit

SOURCE = 'Maximize 3x+2y; 2x+y<=8; x+2y<=8; x,y nonnegative integers. Explain the allocation. Compute another statistic.'


def check():
    return DomainCheck(id='feasible', description='原题约束与目标值独立复算', left={'task_id': 'Q1', 'name': 'objective'},
        operator='linear_feasible', tolerance=1e-8, linear_model=LinearModel(
            variables=[{'name': name, 'source_quote': 'x,y nonnegative integers', 'lower': 0, 'integer': True} for name in ['x', 'y']],
            objective=[3, 2], objective_quote='Maximize 3x+2y',
            constraints=[{'coefficients': [2, 1], 'relation': 'le', 'rhs': 8, 'source_quote': '2x+y<=8'},
                         {'coefficients': [1, 2], 'relation': 'le', 'rhs': 8, 'source_quote': 'x+2y<=8'}]))


def store_for(root, max_attempts=3):
    store = ResearchStore(root, 'linear')
    store.initialize(SOURCE, {'subproblems': [
        {'id': 'Q1', 'question': 'Solve explicit linear integer problem'},
        {'id': 'Q2', 'question': 'Explain allocation', 'depends_on': ['Q1']},
        {'id': 'Q3', 'question': 'Another statistic'}], 'assumptions': [], 'requirements': [
        {'id': 'RQ1', 'statement': 'Feasible allocation', 'source_quote': '2x+y<=8', 'kind': 'constraint', 'task_ids': ['Q1'], 'checks': [check().model_dump()]},
        {'id': 'RQ2', 'statement': 'Explain', 'source_quote': 'Explain the allocation', 'kind': 'goal', 'task_ids': ['Q2']},
        {'id': 'RQ3', 'statement': 'Statistic', 'source_quote': 'Compute another statistic', 'kind': 'goal', 'task_ids': ['Q3']} ]}, max_attempts=max_attempts)
    (root / '.system/graph_thread.json').write_text(json.dumps({'engine_version': ENGINE_VERSION, 'thread_id': 'linear'}))
    return store


def test_false_zero_violation_is_rejected_and_repaired_without_recomputing_unrelated_work(tmp_path):
    store = store_for(tmp_path)
    submit(store, 'Q3', {'statistic': 7})
    unrelated = store.snapshot()['tasks']['Q3']['active_attempt']
    submit(store, 'Q1', {'x': 4, 'y': 4, 'objective': 20, 'max_violation': 0})
    state = store.snapshot()
    assert state['validations'][-1]['outcome'] == 'rejected'
    assert '复算左值 12' in research_findings(state)[0]['message']
    assert not store.delivery_ready(state) and 'Q2' not in ready_tasks(state)
    assert {r['subproblem'] for r in store.registry_payload()['results']} == {'Q3'}
    snapshot = research_snapshot(tmp_path, 'work', 'failed')
    assert snapshot.delivery['ready'] is False
    assert snapshot.tasks[0]['validation']['outcome'] == 'rejected'
    assert any('复算左值 12' in b['message'] and b['action'] == 'recompute' for b in snapshot.delivery['blockers'])
    original = state['spec']['requirements'][0]['checks']
    store.apply_patch(PlanPatch(command_id='repair', base_revision=state['revision'], reason='Respect actual constraints',
        issue_ids=[research_findings(state)[0]['id']], changes=[TaskChange(task_id='Q1', objective='Re-enumerate feasible integer pairs')]))
    assert store.snapshot()['spec']['requirements'][0]['checks'] == original
    assert research_snapshot(tmp_path, 'work', 'failed').tasks[0]['validation'] is None
    submit(store, 'Q1', {'x': 3, 'y': 2, 'objective': 13})
    submit(store, 'Q2', {'allocation_explained': 1})
    state = store.snapshot()
    assert store.delivery_ready(state)
    assert state['tasks']['Q3']['active_attempt'] == unrelated
    assert all(i['status'] == 'resolved' for i in research_findings(state))
    assert research_snapshot(tmp_path, 'work', 'completed').delivery == {'ready': True, 'blockers': []}
    assert '未验证最优性' in research_snapshot(tmp_path, 'work', 'completed').tasks[0]['validation']['checks'][0]['message']


@pytest.mark.parametrize('values,outcome,detail', [
    ({'x': 3, 'y': 2, 'objective': 99}, 'rejected', '复算为 13'),
    ({'x': -1, 'y': 2, 'objective': 1}, 'rejected', '低于下界'),
    ({'x': 2.5, 'y': 2, 'objective': 11.5}, 'rejected', '整数约束'),
    ({'x': 3, 'objective': 13}, 'inconclusive', '缺少唯一登记结果'),
    ({'x': 'nan', 'y': 2, 'objective': 13}, 'inconclusive', '有限数'),
    ({'x': 2, 'y': 2, 'objective': 10}, 'accepted', '未验证最优性'),
])
def test_linear_acceptance_recomputes_bounds_integrality_and_objective(tmp_path, values, outcome, detail):
    store = store_for(tmp_path)
    submit(store, 'Q1', values)
    record = store.snapshot()['validations'][-1]
    assert record['outcome'] == outcome
    assert detail in record['checks'][0]['message']


def test_linear_contract_rejects_unsourced_constraints_and_invalid_dimensions(tmp_path):
    data = check().model_dump()
    data['linear_model']['objective'] = [3]
    with pytest.raises(ValidationError, match='系数'):
        DomainCheck.model_validate(data)
    data = check().model_dump()
    data['linear_model']['variables'][1]['name'] = 'x'
    with pytest.raises(ValidationError, match='不能重复'):
        DomainCheck.model_validate(data)
    store = store_for(tmp_path)
    analysis = {'subproblems': [t['subproblem'] for t in store.snapshot()['tasks'].values()],
        'requirements': [{k: v for k, v in r.items() if k != 'revision'} for r in store.snapshot()['spec']['requirements']], 'assumptions': []}
    analysis['requirements'][0]['checks'][0]['linear_model']['constraints'][0]['source_quote'] = 'invented constraint'
    with pytest.raises(ValueError, match='逐字引用原题'):
        ResearchStore(tmp_path / 'invalid', 'bad').initialize(SOURCE, analysis)


def test_unbounded_sign_equality_lower_constraints_and_absolute_tolerance():
    from ai_system.research.linear import verify_linear
    model = LinearModel(variables=[{'name': 'x', 'source_quote': 'x is real'}], objective=[2], objective_quote='2x', offset=1,
        constraints=[{'coefficients': [1], 'relation': 'ge', 'rhs': -3, 'source_quote': 'x>=-3'},
                     {'coefficients': [2], 'relation': 'eq', 'rhs': -4, 'source_quote': '2x=-4'}])
    assert verify_linear(model, [-2], -3, 1e-8)[0]
    assert verify_linear(model, [-2 - 1e-10], -3, 1e-8)[0]
    assert not verify_linear(model, [-4], -7, 1e-8)[0]


def test_unused_linear_model_does_not_change_frozen_numeric_check_hash():
    raw = {'id': 'finite', 'description': '原有数值验收', 'left': {'task_id': 'Q1', 'name': 'x'},
           'operator': 'finite', 'expected': None, 'right': None, 'tolerance': 0.0}
    from ai_system.research.validation import fingerprint
    assert fingerprint(DomainCheck.model_validate(raw).model_dump()) == fingerprint(raw)


def test_delivery_explains_exhausted_budget_even_when_execution_issue_exists(tmp_path):
    from ai_system.research.models import WorkerResult, IssueReport
    store = store_for(tmp_path, max_attempts=1)
    store.commit(WorkerResult(order=store.claim('Q3'), execution_status='failed', acceptance='inconclusive', report={},
        issues=[IssueReport(task_id='Q3', category='execution', message='kernel stopped')]))
    snapshot = research_snapshot(tmp_path, 'work', 'failed')
    assert any('预算已耗尽' in b['message'] and b['action'] == 'recompute' for b in snapshot.delivery['blockers'])


def test_submission_requires_decisions_not_just_self_reported_residual(tmp_path):
    from ai_system.graph.context import GraphContext
    from ai_system.modules.experiments.execution import submission_schema
    from test_paper_graph import FakeModel
    from test_report_submission import branch, seed, report
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    runs = []
    objective = seed(ctx, runs, name='objective', value='13')
    task = branch()
    task['research_contract']['checks'][0]['check'] = check().model_dump()
    with pytest.raises(ValidationError, match='x'):
        submission_schema(ctx, task, runs).model_validate(report(objective.id))


def test_requirement_feedback_identifies_the_rule_and_preserves_hard_constraints():
    from ai_system.research.models import Requirement
    data = {'id': 'R', 'statement': '输出中文报告', 'source_quote': '中文报告', 'kind': 'constraint', 'task_ids': ['Q1']}
    with pytest.raises(ValidationError, match='输出中文报告.*必须有可执行检查') as error:
        Requirement.model_validate(data)
    assert 'deliverable' in str(error.value)
    assert Requirement.model_validate({**data, 'kind': 'deliverable'}).checks == []
    with pytest.raises(ValidationError):
        Requirement.model_validate({**data, 'statement': '容量不得超过8'})
