from services.file_services.workspace_layout import workspace_path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asyncio
import json
import pytest
from ai_system.graph.context import GraphContext
from helpers.legacy_research import worker_context
from helpers.paper import accepted_store

SELECTED = {'mode': 'selected', 'selected': ['regression'], 'disabled': ['arima']}


def test_selection_invalidates_existing_evidence_without_relaxing_contract(tmp_path):
    store = accepted_store(tmp_path)
    before = store.snapshot()
    result = store.set_skill_preferences(SELECTED, 0)
    after = store.snapshot()
    assert set(result['affected']) == {'Q1','Q2','Q3'}
    assert all(t['validity'] == 'stale' for t in after['experiments'].values())
    from services.chat_services.research_session import research_snapshot, resolve_action
    (workspace_path(tmp_path, '.system/graph_thread.json')).write_text(json.dumps({'thread_id':store.research_id,'engine_version':'research-v3','status':'done'}))
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
