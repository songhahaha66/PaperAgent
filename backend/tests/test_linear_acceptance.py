"""Frozen linear constraints reject fabricated feasibility, and use the existing repair lifecycle."""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.research import ENGINE_VERSION
from ai_system.research.models import DomainCheck, LinearModel
from ai_system.research.store import ResearchStore
from helpers.contracts import submit

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
    analysis = {'subproblems': [t['subproblem'] for t in store.snapshot()['experiments'].values()],
        'requirements': [{k: v for k, v in r.items() if k != 'revision'} for r in store.snapshot()['spec']['requirements']], 'assumptions': []}
    analysis['requirements'][0]['checks'][0]['linear_model']['constraints'][0]['source_quote'] = 'invented constraint'
    with pytest.raises(ValueError, match='逐字引用原题'):
        ResearchStore(tmp_path / 'invalid', 'bad').initialize(SOURCE, analysis)
