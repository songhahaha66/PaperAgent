"""Lightweight entry, lazy execution contracts and recovery boundaries (offline)."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from ai_system.graph.context import GraphContext
from ai_system.graph.build import compile_graph
from ai_system.research import control, loop, planner
from ai_system.research.models import PlanPatch, Requirement, ResearchConflict, TaskChange
from ai_system.research.store import ResearchStore

SOURCE = '计算需求。容量最多10；保留完整数据和代码，中文报告。'


def setup(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    ctx.research = ResearchStore(tmp_path, 'lightweight')
    return ctx


def execute():
    return planner.EvidenceDecision(action='execute', reason='先计算本题需求。', requirement_ids=['RQ0'],
        task_ids=['Q1'], changes=[TaskChange(action='add', task_id='Q1', title='需求',
            objective='计算需求', dependencies=[], requirement_ids=['RQ0'])])


def apply(store, proposal, command='bind'):
    return store.apply_patch(PlanPatch(command_id=command, base_revision=store.snapshot()['revision'],
        reason=proposal.reason, changes=proposal.changes, requirements=proposal.requirements))


def test_start_persists_source_materials_and_output_without_a_plan(tmp_path):
    ctx = setup(tmp_path)
    attachment = tmp_path/'attachment'/'demand.csv'
    attachment.parent.mkdir(exist_ok=True)
    attachment.write_text('x\n10\n')
    snapshot = ctx.research.initialize(SOURCE, output_mode='latex')
    assert not snapshot['tasks'] and not snapshot['attempts'] and not snapshot['assumptions']
    assert snapshot['spec']['requirement'] == SOURCE
    assert snapshot['spec']['requirements'][0]['statement'] == SOURCE
    assert snapshot['spec']['output_mode'] == 'latex'
    assert 'attachment/demand.csv' in snapshot['materials']['inputs']
    assert snapshot['materials']['profile'] == ''
    assert ctx.research.initialize('do not replace source') == snapshot
    public = control.public_snapshot(snapshot)
    assert public['requirements'][0]['status'] == 'pending' and not public['delivery']['ready']
    nodes = compile_graph().get_graph().nodes
    assert 'observe' in nodes and 'analyze' not in nodes
    from ai_system.modules.registry import capabilities
    assert 'experiments.analyze' not in {c['name'] for c in capabilities().describe()}


def test_first_observe_can_inspect_without_tasks_or_extra_model_calls(tmp_path, monkeypatch):
    ctx = setup(tmp_path)
    calls = []
    async def decide(self, snapshot, context):
        calls.append(context)
        assert not context['tasks'] and not context['evidence'] and not context['ready_tasks']
        return planner.EvidenceDecision(action='inspect', reason='先查看材料是否能回答需求。', requirement_ids=['RQ0'])
    monkeypatch.setattr(planner.Planner, 'decide', decide)
    state = asyncio.run(control.observe({'task':SOURCE}, SimpleNamespace(context=ctx)))
    action = loop.select(state, SimpleNamespace(context=ctx))
    assert action.arg['capability'] == 'experiments.inspect'
    assert len(calls) == 1 and not ctx.research.snapshot()['tasks']
    assert state['analysis']['subproblems'] == [] and state['analysis']['restatement'] == SOURCE
    assert state['data_profile'] == ''


def test_first_decision_creates_and_dispatches_one_task_with_fresh_worker_inputs(tmp_path, monkeypatch):
    ctx = setup(tmp_path)
    calls = []
    async def decide(self, snapshot, context):
        calls.append(context)
        return execute()
    monkeypatch.setattr(planner.Planner, 'decide', decide)
    state = asyncio.run(control.observe({'task':SOURCE}, SimpleNamespace(context=ctx)))
    actions = loop.select(state, SimpleNamespace(context=ctx))
    branch = actions[0].arg['inputs']['branch']
    assert len(calls) == len(actions) == 1
    assert branch['source'] == SOURCE and branch['subproblem']['id'] == 'Q1'
    assert branch['analysis']['subproblems'] == [branch['subproblem']]
    assert branch['research_contract']['requirements'][0]['task_ids'] == ['Q1']
    assert not branch['dependencies'] and not branch['research_contract']['checks']
    assert len(ctx.research.snapshot()['tasks']) == 1
    key = actions[0].arg['decision_key']
    receipt = ctx.research.planning_decision(key+':apply')
    repeated = ctx.research.apply_patch(PlanPatch(command_id=key+':apply', base_revision=1,
        reason=execute().reason, changes=execute().changes))
    assert repeated == receipt and not ctx.research.snapshot()['attempts']


def test_source_constraint_can_wait_but_execution_requires_frozen_checks(tmp_path):
    ctx = setup(tmp_path)
    store = ctx.research
    store.initialize(SOURCE)
    constraint = Requirement(id='RQ1', statement='容量最多10', source_quote='容量最多10', kind='constraint')
    store.apply_patch(PlanPatch(command_id='retain', base_revision=1, reason='保留明确容量约束，尚未做实验。', requirements=[constraint]))
    proposal = execute()
    proposal.requirement_ids.append('RQ1')
    proposal.changes[0].requirement_ids.append('RQ1')
    key, context = planner.decision_context(store, store.snapshot())
    with pytest.raises(ValueError, match='执行前'):
        planner.validate_decision(proposal, store.snapshot(), context)
    apply(store, proposal)
    with pytest.raises(ResearchConflict, match='操作化检查'):
        store.claim('Q1')
    current = next(r for r in store.snapshot()['spec']['requirements'] if r['id']=='RQ1')
    current.pop('revision')
    current['checks'] = [{'id':'capacity', 'description':'容量上限', 'left':{'task_id':'Q1', 'name':'需求'}, 'operator':'le', 'expected':10}]
    store.apply_patch(PlanPatch(command_id='checks', base_revision=store.snapshot()['revision'],
        reason='执行前按原题冻结检查。', requirements=[Requirement.model_validate(current)]))
    order = store.claim('Q1')
    frozen = copy.deepcopy(store.snapshot()['attempts'][order.attempt_id])
    changed = copy.deepcopy(current)
    changed['checks'][0]['expected'] = 9
    with pytest.raises(ValueError, match='冻结检查'):
        store.apply_patch(PlanPatch(command_id='rewrite', base_revision=store.snapshot()['revision'],
            reason='不能看结果后变更阈值。', requirements=[Requirement.model_validate(changed)]))
    changed = copy.deepcopy(current)
    changed['checks'].append({**current['checks'][0], 'id':'posthoc'})
    with pytest.raises(ValueError, match='执行后'):
        store.apply_patch(PlanPatch(command_id='append', base_revision=store.snapshot()['revision'],
            reason='不能执行后添加检查。', requirements=[Requirement.model_validate(changed)]))
    assert store.snapshot()['attempts'][order.attempt_id] == frozen
    assert not store.delivery_ready(store.snapshot())


@pytest.mark.parametrize('legacy_analysis', [False, True])
def test_removed_entry_checkpoints_resume_at_observe_preserving_saved_source(tmp_path, legacy_analysis):
    from ai_system.graph.agent import PaperGraphAgent
    from test_paper_graph import _analysis, TASK
    source = TASK if legacy_analysis else SOURCE
    saved = {'task':source}
    if legacy_analysis:
        saved['analysis'] = _analysis().model_dump()
    ctx = setup(tmp_path)
    agent = PaperGraphAgent(None, workspace_dir=str(tmp_path), judge=None)
    updates = []
    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values=saved, next=('analyze',))
        async def aupdate_state(self, config, values, as_node):
            updates.append((values, as_node))
    async def invoke(*args):
        return 'resumed'
    agent._invoke = invoke
    assert asyncio.run(agent._resume_graph(Graph(), {}, 'lightweight', ctx)) == 'resumed'
    snapshot = ctx.research.snapshot()
    assert snapshot['spec']['requirement'] == source
    assert bool(snapshot['tasks']) == legacy_analysis
    assert updates == [({'research_blocked':False}, 'solve_join')]
