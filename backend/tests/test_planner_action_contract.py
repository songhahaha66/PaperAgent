"""Unified problem creation, atomic dispatch and recoverable format failures."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from ai_system.graph import llm_utils
from ai_system.research.loop import prepare_action, resume, commit_decision
from ai_system.research.planner import NextAction, decision_context, validate_action
from ai_system.research.store import ResearchStore
from ai_system.research.runtime import ResearchRuntime


EXPERIMENT = {
    'action': 'experiment', 'reason': '比较实测验证误差',
    'question_updates': [{'question': '怎样预测下一周需求？'}],
    'method': '计算移动平均与线性趋势预测',
    'conditions': '前15天训练，后5天固定验证',
    'evaluation': '同一验证集上的MAE与RMSE',
}


@pytest.mark.parametrize('field', ['method', 'conditions', 'evaluation'])
@pytest.mark.parametrize('value', [None, '', ' \n '])
def test_incomplete_experiment_identifies_missing_field(field, value):
    candidate = dict(EXPERIMENT)
    if value is None:candidate.pop(field)
    else:candidate[field] = value
    with pytest.raises(ValidationError, match=field):NextAction.model_validate(candidate)


def context(tmp_path, model=None):
    store = ResearchStore(tmp_path, 'research')
    store.initialize('比较移动平均与线性趋势，最后5天验证，预测下一周并保留代码图表。')
    events = []
    async def stage(message):events.append(message)
    async def emit(*args):pass
    return SimpleNamespace(research=store, planner_llm=model, max_planning_calls=None,
                           events=SimpleNamespace(stage=stage,emit=emit)), events


def test_schema_has_one_problem_creation_entry_and_system_association():
    schema = convert_to_openai_tool(NextAction)['function']['parameters']
    assert 'question' not in schema['properties']
    assert {'question_updates','question_id','question_index'} <= schema['properties'].keys()
    assert schema['additionalProperties'] is False
    with pytest.raises(ValidationError, match='多个问题'):
        NextAction.model_validate({**EXPERIMENT,'question_updates':[{'question':'A?'},{'question':'B?'}]})


def test_first_decision_creates_and_links_selected_problem_atomically(tmp_path):
    ctx,_=context(tmp_path);before=ctx.research.snapshot();key,view=decision_context(ctx.research,before)
    proposal=NextAction.model_validate({**EXPERIMENT,'question_updates':[{'question':'A?'},{'question':'B?'}],'question_index':1})
    receipt=commit_decision(ctx.research,proposal,view,base_revision=before['revision'],key=key,observation_id='observe:first')
    after=ctx.research.snapshot();eid=receipt['experiment_id'];qid=after['experiments'][eid]['question_id']
    assert len(after['questions'])==2 and after['questions'][qid]['question']=='B?'
    assert after['action']['target']==eid and after['action']['question_id']==qid
    assert after['revision']==before['revision']+1 and not after['executions']
    assert commit_decision(ctx.research,proposal,view,base_revision=before['revision'],key=key,observation_id='observe:first')==receipt
    assert ctx.research.snapshot()==after


def test_invalid_linked_decision_cannot_leave_half_created_problem(tmp_path):
    ctx,_=context(tmp_path);before=ctx.research.snapshot();key,view=decision_context(ctx.research,before)
    proposal=NextAction(action='tool',reason='Correct tool parameters',question_updates=[{'question':'A?'}],capability='missing.tool')
    with pytest.raises((ValueError,KeyError)):
        commit_decision(ctx.research,proposal,view,base_revision=before['revision'],key=key,observation_id='observe:bad')
    assert ctx.research.snapshot()==before


@pytest.mark.parametrize('json_first', [True, False])
def test_planner_repairs_exact_candidate_without_duplicate_problem_field(tmp_path, json_first):
    incomplete = {k:v for k,v in EXPERIMENT.items() if k!='question_updates'}
    class Model:
        model_name = 'Qwen-test' if json_first else 'tool-test'
        calls = 0
        def with_structured_output(self,schema,**kwargs):self.schema=schema;return self
        async def ainvoke(self,messages):
            self.calls+=1
            if self.calls==1:
                if json_first:return AIMessage(content=json.dumps(incomplete,ensure_ascii=False))
                try:self.schema.model_validate(incomplete)
                except ValidationError as error:
                    return {'raw':AIMessage(content='',tool_calls=[{'name':'NextAction','args':incomplete,'id':'initial'}]),
                            'parsed':None,'parsing_error':error}
            assert 'question_updates' in messages[-1].content
            assert json.loads(messages[-2].content)==incomplete
            assert not ctx.research.snapshot()['questions'] and not ctx.research.snapshot()['experiments']
            return AIMessage(content=json.dumps(EXPERIMENT,ensure_ascii=False))
    model=Model();ctx,_=context(tmp_path,model)
    assert not asyncio.run(prepare_action(ctx))
    state=ctx.research.snapshot();experiment=state['experiments'][state['action']['target']]
    assert model.calls==2 and state['action']['capability']=='experiments.execute'
    assert state['questions'][experiment['question_id']]['question']==EXPERIMENT['question_updates'][0]['question']
    assert not state['executions']


def test_failed_candidate_keeps_error_and_resumes_without_persistent_format_guard(tmp_path):
    class Model:
        model_name='Qwen-test'
        calls=0
        async def ainvoke(self,messages):
            self.calls+=1
            data={**EXPERIMENT,'conditions':'','evaluation':''} if self.calls<=llm_utils.RETRIES+1 else EXPERIMENT
            return AIMessage(content=json.dumps(data,ensure_ascii=False))
    model=Model();ctx,events=context(tmp_path,model)
    assert asyncio.run(prepare_action(ctx))
    state=ctx.research.snapshot();failure=state['action']
    assert failure['status']=='failed' and failure['phase']=='decision' and not state['experiments']
    assert 'conditions' in failure['error'] and 'evaluation' in failure['error']
    assert json.loads(failure['candidate']['response'])['conditions']==''
    runtime=ResearchRuntime(ctx.research.root,ctx.research.research_id)
    with runtime.store._connect() as db:assert not db.execute('SELECT 1 FROM failure_guards').fetchone()
    resume(ctx.research)
    assert ctx.research.snapshot()['action']['candidate']==failure['candidate']
    assert not asyncio.run(prepare_action(ctx))
    assert len(ctx.research.snapshot()['experiments'])==1 and model.calls==llm_utils.RETRIES+2
