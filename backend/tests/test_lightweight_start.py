"""Lightweight entry, lazy execution contracts and recovery boundaries (offline)."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from ai_system.graph.context import GraphContext
from ai_system.graph.build import compile_graph
from ai_system.research import control, loop, planner
from ai_system.research.models import Requirement, ResearchConflict
from ai_system.research.store import ResearchStore

SOURCE = '计算需求。容量最多10；保留完整数据和代码，中文报告。'

def setup(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    ctx.research = ResearchStore(tmp_path, 'lightweight')
    return ctx

def test_start_persists_source_materials_and_output_without_a_plan(tmp_path):
    ctx = setup(tmp_path)
    attachment = tmp_path/'attachment'/'demand.csv'
    attachment.parent.mkdir(exist_ok=True)
    attachment.write_text('x\n10\n')
    snapshot = ctx.research.initialize(SOURCE, output_mode='latex')
    assert not snapshot['experiments'] and not snapshot['executions'] and not snapshot['assumptions']
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
        assert not context['experiments'] and not context['artifacts']
        return planner.NextAction(action='inspect', reason='先查看材料是否能回答需求。')
    monkeypatch.setattr(planner.Planner, 'decide', decide)
    state = asyncio.run(control.observe({'task':SOURCE}, SimpleNamespace(context=ctx)))
    action = loop.select(state, SimpleNamespace(context=ctx))
    assert ctx.research.snapshot()['action']['capability'] == 'experiments.inspect'
    assert len(calls) == 1 and not ctx.research.snapshot()['experiments']
    assert state['analysis']['subproblems'] == [] and state['analysis']['restatement'] == SOURCE
    assert state['data_profile'] == ''
