"""Incomplete initial experiments must be repaired before any execution is queued."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.graph import llm_utils
from ai_system.research.loop import prepare_action, resume
from ai_system.research.planner import NextAction, validate_action
from ai_system.research.store import ResearchStore


EXPERIMENT = {
    'action': 'experiment', 'reason': '比较实测验证误差',
    'question': '怎样预测下一周需求？',
    'method': '计算移动平均与线性趋势预测',
    'conditions': '前15天训练，后5天固定验证',
    'evaluation': '同一验证集上的MAE与RMSE',
}


@pytest.mark.parametrize('field', ['question', 'method', 'conditions', 'evaluation'])
@pytest.mark.parametrize('value', [None, '', ' \n '])
def test_incomplete_experiment_identifies_missing_field(field, value):
    candidate = dict(EXPERIMENT)
    if value is None:
        candidate.pop(field)
    else:
        candidate[field] = value
    with pytest.raises(ValidationError) as caught:
        NextAction.model_validate(candidate)
    error = caught.value.errors(include_url=False)[0]
    assert error['type'] == 'experiment_fields_missing'
    assert field in error['msg']


def test_new_existing_and_protocol_experiments_preserve_execution_contracts():
    state = {'questions': {'Q1': {}}, 'experiments': {'E1': {'execution_status': 'failed'}}}
    context = {'artifacts': [], 'issues': []}
    proposals = [
        EXPERIMENT,
        {**EXPERIMENT, 'question': '', 'question_id': 'Q1'},
        {'action': 'experiment', 'reason': '重试执行故障', 'experiment_id': 'E1'},
        {'action': 'experiment', 'reason': '执行已固定协议', 'question': EXPERIMENT['question'],
         'experiment': {'conditions': EXPERIMENT['conditions'], 'evaluation': EXPERIMENT['evaluation'],
                        'candidates': [{'name': '移动平均', 'instruction': '计算固定窗口均值'}]}},
        {'action': 'inspect', 'reason': '先查看材料'},
        {'action': 'ask', 'reason': '缺少必要数据', 'message': '请提供销量数据'},
    ]
    for proposal in proposals:
        validate_action(NextAction.model_validate(proposal), state, context)
    with pytest.raises(ValueError, match='方法已冻结'):
        validate_action(NextAction.model_validate({**EXPERIMENT, 'experiment_id': 'E1'}), state, context)
    with pytest.raises(ValueError, match='已有研究问题'):
        validate_action(NextAction.model_validate({**EXPERIMENT, 'question_id': 'unknown'}), state, context)


def test_model_schema_includes_requirements_for_experiments_only():
    # Check the actual schema sent through LangChain's tool adapter, not just
    # Pydantic's local validator. Retries and other actions need no new method.
    schema = convert_to_openai_tool(NextAction)['function']['parameters']
    condition = schema['allOf'][0]
    assert condition['if']['properties']['action'] == {'const': 'experiment'}
    question, method = condition['then']['allOf']
    assert {branch['required'][0] for branch in question['anyOf']} == {
        'experiment_id', 'question_id', 'question'}
    assert method['anyOf'][-1]['required'] == ['method', 'conditions', 'evaluation']
    assert schema['additionalProperties'] is False


def context(tmp_path, model):
    store = ResearchStore(tmp_path, 'research')
    store.initialize('比较移动平均与线性趋势，最后5天验证，预测下一周并保留代码图表。')
    events = []
    async def stage(message):
        events.append(message)
    return SimpleNamespace(research=store, planner_llm=model, max_planning_calls=None,
                           events=SimpleNamespace(stage=stage)), events


@pytest.mark.parametrize('json_first', [True, False])
def test_planner_repairs_exact_candidate_then_queues_complete_experiment(tmp_path, monkeypatch, json_first):
    monkeypatch.setattr(llm_utils, 'RETRY_DELAY', 0)
    incomplete = {k: v for k, v in EXPERIMENT.items() if k != 'question'}

    class Model:
        model_name = 'Qwen-test' if json_first else 'tool-test'
        calls = 0

        def with_structured_output(self, schema, **kwargs):
            self.schema = schema
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                if json_first:
                    return AIMessage(content=json.dumps(incomplete, ensure_ascii=False))
                try:
                    self.schema.model_validate(incomplete)
                except ValidationError as error:
                    return {'raw': AIMessage(content='', tool_calls=[{
                        'name': 'NextAction', 'args': incomplete, 'id': 'initial'}]),
                        'parsed': None, 'parsing_error': error}
            assert 'question' in messages[-1].content and '缺少必填信息' in messages[-1].content
            assert json.loads(messages[-2].content) == incomplete
            assert not ctx.research.snapshot()['experiments']
            return AIMessage(content=json.dumps(EXPERIMENT, ensure_ascii=False))

    model = Model()
    ctx, _ = context(tmp_path, model)
    assert not asyncio.run(prepare_action(ctx))
    state = ctx.research.snapshot()
    experiment = state['experiments'][state['action']['target']]
    assert model.calls == 2 and state['action']['capability'] == 'experiments.execute'
    assert experiment['experiment']['conditions'] == EXPERIMENT['conditions']
    assert experiment['experiment']['evaluation'] == EXPERIMENT['evaluation']
    assert state['questions'][experiment['question_id']]['question'] == EXPERIMENT['question']
    assert not state['executions']


def test_failed_repair_preserves_exact_missing_fields_and_can_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_utils, 'RETRY_DELAY', 0)

    class Model:
        model_name = 'Qwen-test'
        calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            data = {**EXPERIMENT, 'conditions': '', 'evaluation': ''} if self.calls <= 2 else EXPERIMENT
            return AIMessage(content=json.dumps(data, ensure_ascii=False))

    model = Model()
    ctx, events = context(tmp_path, model)
    assert asyncio.run(prepare_action(ctx))
    state = ctx.research.snapshot()
    assert state['action']['status'] == 'failed' and not state['experiments']
    assert '新实验缺少必填信息：conditions、evaluation' in state['action']['message']
    assert '尚未开始实验' in events[-1]
    source = state['spec']
    resume(ctx.research)
    assert ctx.research.snapshot()['spec'] == source
    assert not asyncio.run(prepare_action(ctx))
    assert len(ctx.research.snapshot()['experiments']) == 1
    assert ctx.research.snapshot()['spec']['requirement'] == source['requirement']
    assert ctx.research.snapshot()['spec']['amendments'] == source['amendments']
    assert model.calls == 3
