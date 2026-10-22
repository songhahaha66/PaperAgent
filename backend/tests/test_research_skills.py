import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asyncio
import json
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ai_system.modules.methods import skill_catalog, skill_detail, describe, methods_catalog, validate_skill_policy
from ai_system.graph.context import GraphContext
from ai_system.modules.experiments.planning import experiment_design
from ai_system.modules.experiments.execution import build_task
from ai_system.graph.state import MethodPick, CandidateSet, ModelCandidate
from ai_system.research.models import ResearchConflict
from ai_system.research.store import ResearchStore
from ai_system.research.control import worker_context
from helpers.paper import accepted_store
from helpers.contracts import problem, TASK

SELECTED = {'mode': 'selected', 'selected': ['regression'], 'disabled': ['arima']}


def test_catalog_is_metadata_and_details_load_on_demand():
    catalog = skill_catalog()
    assert len(catalog) == 6 and all('instructions' not in s for s in catalog)
    for item in catalog:
        detail = skill_detail(item['key'])
        assert detail['instructions'] and detail['sources'][0]['url'].startswith('https://')
    assert 'ARIMA 时间序列' not in methods_catalog(SELECTED)
    reference = describe(['regression', 'arima'], SELECTED)
    assert 'statsmodels.org/stable/regression.html' in reference and '验收提示' in reference
    assert 'statsmodels.tsa.arima' not in reference


@pytest.mark.parametrize('policy', [
    {'mode':'selected','selected':[],'disabled':[]},
    {'mode':'selected','selected':['regression'],'disabled':['regression']},
    {'mode':'auto','selected':[],'disabled':['unknown']},
])
def test_invalid_selection_is_rejected(policy):
    with pytest.raises(ValueError): validate_skill_policy(policy)


def test_selection_before_research_loads_into_context(tmp_path):
    store = ResearchStore(tmp_path, 'workspace-skills')
    result = store.set_skill_preferences(SELECTED, 0)
    assert result['version'] == 1 and result['affected'] == []
    assert store.snapshot() is None
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    assert ctx.skill_policy == SELECTED
    assert 'skills:policy' in store.file_inputs()
    with pytest.raises(ResearchConflict): store.set_skill_preferences(SELECTED, 0)


def test_selection_invalidates_existing_evidence_without_relaxing_contract(tmp_path):
    store = accepted_store(tmp_path)
    before = store.snapshot()
    result = store.set_skill_preferences(SELECTED, 0)
    after = store.snapshot()
    assert set(result['affected']) == {'Q1','Q2','Q3'}
    assert all(t['validity'] == 'stale' for t in after['experiments'].values())
    from services.chat_services.research_session import research_snapshot, resolve_action
    (tmp_path / '.system/graph_thread.json').write_text(json.dumps({'thread_id':store.research_id,'engine_version':'research-v3','status':'done'}))
    snapshot = research_snapshot(tmp_path, 'demo', 'completed')
    assert snapshot.work_status == 'paused' and asyncio.run(resolve_action('resume', '继续', snapshot)) == 'resume'
    assert after['spec'] == before['spec'] and after['assumptions'] == before['assumptions']
    assert len(after['executions']) == len(before['executions'])
    assert store.set_skill_preferences(SELECTED, 1)['affected'] == []
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    ctx.research = store
    from research_tools_helpers import add_experiment,continue_experiment
    assert not store.registry_payload()['results']
    assert after['artifacts']==before['artifacts']
    for aid,execution in before['executions'].items():
        assert after['executions'][aid]['result']==execution['result']
    with pytest.raises(ValueError,match='失效'):continue_experiment(store,'Q1')
    eid=add_experiment(store,question_id=after['experiments']['Q1']['question_id'],
        method='使用当前选择的回归技能求解',conditions='当前数据与所选regression技能策略',evaluation='核对当前问题与实际执行输出')
    order = store.claim(eid)
    local = worker_context(ctx, order)
    assert order.task_id!= 'Q1' and store.snapshot()['experiments']['Q1']['validity']=='stale'
    assert store.snapshot()['executions'][before['experiments']['Q1']['execution_id']]['input_refs']==before['executions'][before['experiments']['Q1']['execution_id']]['input_refs']
    assert local.skill_policy == SELECTED
    assert order.input_refs['skills:policy'] == store.file_inputs()['skills:policy']


def test_controller_method_and_solver_receive_selected_skill_and_source(tmp_path):
    ctx=GraphContext.create(str(tmp_path),planner_llm=None,skill_policy=SELECTED)
    branch={'source':TASK,'analysis':problem().model_dump(),'subproblem':problem().model_dump()['subproblems'][0],'data_profile':'columns x, y'}
    plan={'conditions':'给定数据','evaluation':'验证误差','candidates':[{'name':'线性回归','instruction':'检验变量关系','method_keys':['regression']}]}
    record=experiment_design(plan)
    text=build_task({**branch,'design':record,'experiment':plan},ctx)
    assert '回归建模技能' in text and 'https://www.statsmodels.org/stable/regression.html' in text
    assert '禁用：arima' in text and '检验变量关系' in text
    with pytest.raises(ValueError,match='禁用'):
        build_task({**branch,'design':{'chosen':{**record['chosen'],'method_keys':['arima']}}},ctx)



def test_skill_api_persists_selection_and_checks_owner_and_version(tmp_path, monkeypatch):
    from routers.work_routes import workspace
    from auth.auth import get_current_user
    from database.database import get_db
    app = FastAPI(); app.include_router(workspace.router)
    app.dependency_overrides[get_current_user] = lambda:7
    app.dependency_overrides[get_db] = lambda:object()
    monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(created_by=7))
    monkeypatch.setattr(workspace.workspace_file_service,'get_workspace_path',lambda *a:tmp_path)
    with TestClient(app) as client:
        state = client.get('/api/workspace/demo/skills').json()
        assert len(state['catalog']) == 6 and not state['has_research']
        assert client.get('/api/workspace/demo/skills/regression').json()['instructions']
        request = {'base_version':0,'policy':SELECTED}
        assert client.put('/api/workspace/demo/skills',json=request).status_code == 200
        assert client.get('/api/workspace/demo/skills').json()['policy'] == SELECTED
        assert client.put('/api/workspace/demo/skills',json=request).status_code == 409
        assert client.get('/api/workspace/demo/skills/unknown').status_code == 404
        monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(created_by=8))
        assert client.get('/api/workspace/demo/skills').status_code == 403
        assert client.get('/api/workspace/demo/skills/regression').status_code == 403
        assert client.put('/api/workspace/demo/skills',json=request).status_code == 403
