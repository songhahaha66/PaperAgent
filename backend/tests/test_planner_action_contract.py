"""Real Agent tool loop with controlled replies; no external API validation claim."""
import asyncio
import json
from types import SimpleNamespace
import pytest
from langchain_core.messages import AIMessage,ToolMessage
from ai_system.graph.context import GraphContext
from ai_system.modules.registry import capabilities,invoke
from ai_system.research.loop import prepare_action,resume
from ai_system.research.runtime import execution_scope,ResearchRuntime,ModelServiceError
from ai_system.research.store import ResearchStore
from research_tools_helpers import ResearchToolsModel,tool,add_question

PLAN={'conditions':'entire supplied source','evaluation':'retain original record identity',
      'candidates':[{'name':'source count','instruction':'Count supplied observations'}]}


def context(tmp_path,respond):
    model=ResearchToolsModel(respond=respond)
    ctx=GraphContext.create(str(tmp_path),planner_llm=model)
    ctx.research=ResearchStore(tmp_path,'tools');ctx.research.initialize('Count source observations and describe the selected subjects.')
    return ctx,model


def results(messages):return [m for m in messages if isinstance(m,ToolMessage)]


def test_no_master_decision_or_question_index_in_tool_schemas():
    schemas=capabilities().research_tools()
    assert {'research.update_question','research.schedule_experiment','research.update_answer','research.deliver'}<={s['name'] for s in schemas}
    assert all('question_index' not in json.dumps(s) and 'NextAction' not in json.dumps(s) for s in schemas)
    assert not any(s['name']=='research.update' for s in schemas)


@pytest.mark.parametrize('extra', [{'question_index':0},{'question':'old top level duplicate'},{'question_updates':[]}])
def test_create_then_schedule_and_correct_extra_fields_in_same_agent(tmp_path,extra):
    def respond(messages):
        rows=results(messages)
        if not rows:return tool('research_update_question',{'question':'What do the supplied source records establish?'},'create')
        if len(rows)==1:
            qid=json.loads(rows[-1].content)['question_id']
            assert qid.startswith('question:') and len(ctx.research.snapshot()['questions'])==1
            return tool('research_schedule_experiment',{'question_id':qid,'plan':PLAN,**extra},'bad')
        assert rows[-1].status=='error' and next(iter(extra)) in rows[-1].content
        qid=json.loads(rows[0].content)['question_id']
        assert not ctx.research.snapshot()['experiments']
        return tool('research_schedule_experiment',{'question_id':qid,'plan':PLAN},'fixed')
    ctx,model=context(tmp_path,respond)
    assert not asyncio.run(prepare_action(ctx))
    state=ctx.research.snapshot();eid=state['action']['target']
    assert model.calls==3 and state['action']['capability']=='experiments.execute'
    assert len(state['questions'])==len(state['experiments'])==1 and not state['executions']
    assert state['experiments'][eid]['question_id']==next(iter(state['questions']))
    errors=[a for a in state['activity'] if a['status']=='failed']
    assert len(errors)==1 and errors[0]['inputs']['question_id']==next(iter(state['questions']))
    assert next(iter(extra)) in errors[0]['message']
    transcript=next((tmp_path/'.system/transcripts').glob('*.json')).read_text()
    assert 'fixed' in transcript and 'bad' in transcript


def test_old_candidate_with_unknown_id_is_rejected_then_corrected(tmp_path):
    def respond(messages):
        rows=results(messages)
        if not rows:return tool('research_schedule_experiment',{'question_id':'question:old-candidate','plan':PLAN},'old')
        assert rows[-1].status=='error' and 'question_id' in rows[-1].content
        assert not ctx.research.snapshot()['experiments']
        return tool('research_schedule_experiment',{'question_id':qid,'plan':PLAN},'new')
    ctx,model=context(tmp_path,respond)
    qid=add_question(ctx.research,'Selected subjects');other=add_question(ctx.research,'Aggregate records')
    assert not asyncio.run(prepare_action(ctx))
    state=ctx.research.snapshot()
    assert len(state['questions'])==2 and len(state['experiments'])==1 and model.calls==2
    assert state['experiments'][state['action']['target']]['question_id']==qid!=other


def test_question_committed_before_later_tool_failure_and_explicit_resume_keeps_usage(tmp_path):
    corrected=False
    def respond(messages):
        runtime.reserve('controlled-model')
        rows=results(messages)
        if corrected:
            qid=next(iter(ctx.research.snapshot()['questions']))
            return tool('research_schedule_experiment',{'question_id':qid,'plan':PLAN},'resumed')
        if not rows:return tool('research_update_question',{'question':'Current goal'},'create')
        if len(rows)==1:return tool('research_schedule_experiment',{'question_id':'stale-id','plan':PLAN},'failed-candidate')
        assert rows[-1].status=='error'
        raise ModelServiceError('Controlled service interruption')
    ctx,model=context(tmp_path,respond);runtime=ResearchRuntime(tmp_path,'tools')
    assert asyncio.run(prepare_action(ctx))
    before=ctx.research.snapshot()
    assert len(before['questions'])==1 and not before['experiments'] and not before['executions']
    assert runtime.status()['used']==3
    assert any(a.get('inputs',{}).get('question_id')=='stale-id' and a['status']=='failed' for a in before['activity'])
    resume(ctx.research);corrected=True
    assert not asyncio.run(prepare_action(ctx))
    after=ctx.research.snapshot()
    assert after['questions']==before['questions'] and len(after['experiments'])==1 and not after['executions']
    assert runtime.status()['used']==model.calls==4
    with ctx.research._connect() as db:assert not db.execute('SELECT 1 FROM failure_guards').fetchone()


def test_unknown_old_master_tool_returns_error_then_normal_tool_works(tmp_path):
    def respond(messages):
        rows=results(messages)
        if not rows:return tool('NextAction',{'action':'experiment'},'old-protocol')
        assert rows[-1].status=='error' and '未知工具' in rows[-1].content
        return tool('research_pause',{'message':'No supplied observations yet'},'pause')
    ctx,model=context(tmp_path,respond)
    assert asyncio.run(prepare_action(ctx))
    assert ctx.research.snapshot()['action']['capability']=='pause' and model.calls==2


def test_each_state_tool_is_idempotent_and_invalid_experiment_does_not_rollback_question(tmp_path):
    ctx,_=context(tmp_path,lambda messages:AIMessage(content='unused'))
    async def run():
        with execution_scope(action_id='question-call',tool_call_id='question-call'):
            first=await invoke(ctx,'research.update_question',question='Goal')
            assert await invoke(ctx,'research.update_question',question='Goal')==first
        with pytest.raises(ValueError):
            await invoke(ctx,'research.schedule_experiment',question_id=first['question_id'],plan={**PLAN,'conditions':''})
        return first
    first=asyncio.run(run());state=ctx.research.snapshot()
    assert list(state['questions'])==[first['question_id']] and not state['experiments']


def test_no_progress_final_reply_cannot_spin_empty_observe(tmp_path):
    prompts=[]
    def respond(messages):
        prompts.append(messages)
        return AIMessage(content='Thinking without changes')
    ctx,model=context(tmp_path,respond)
    assert asyncio.run(prepare_action(ctx))
    action=ctx.research.snapshot()['action']
    assert action['error_type']=='NoResearchProgress' and model.calls==2
    assert '尚未调用工具' in prompts[1][-1].content
    assert 'validation error' not in action['message']


def test_plain_text_intention_gets_one_action_feedback_then_uses_real_question_id(tmp_path):
    prompts=[]
    def respond(messages):
        prompts.append(messages)
        if len(prompts)==1:return AIMessage(content='I will inspect the supplied data first.')
        if len(prompts)==2:
            assert '尚未调用工具' in messages[-1].content
            assert not ctx.research.snapshot()['action']
            return tool('research_update_question',{'question':'What do the supplied records establish?'},'question')
        rows=results(messages)
        qid=json.loads(rows[-1].content)['question_id']
        return tool('research_schedule_experiment',{'question_id':qid,'plan':PLAN},'experiment')
    ctx,model=context(tmp_path,respond)
    assert not asyncio.run(prepare_action(ctx))
    state=ctx.research.snapshot()
    assert model.calls==3 and len(state['questions'])==len(state['experiments'])==1
    assert state['action']['capability']=='experiments.execute' and not state['executions']
    assert all(a['status']!='failed' for a in state['activity'])


def test_mixed_valid_and_invalid_calls_receive_observations_before_next_model_request(tmp_path):
    prompts=[]
    def respond(messages):
        prompts.append(messages)
        if len(prompts)==1:
            return AIMessage(content='',
                tool_calls=[{'name':'research_update_question','args':{'question':'Current goal'},'id':'good'}],
                invalid_tool_calls=[{'name':'research_schedule_experiment','args':'{"question_id":','id':'bad','error':'invalid JSON'}],
                additional_kwargs={'tool_calls':[{'id':'bad','type':'function','function':{
                    'name':'research_schedule_experiment','arguments':'{"question_id":'}}]})
        requested={c['id'] for m in messages for c in [*getattr(m,'tool_calls',[]),*getattr(m,'invalid_tool_calls',[])] if c.get('id')}
        responded={m.tool_call_id for m in results(messages)}
        assert requested<=responded and requested=={'good'}
        assert all(not getattr(m,'invalid_tool_calls',[]) for m in messages)
        assert all('tool_calls' not in m.additional_kwargs for m in messages)
        from langchain_openai.chat_models.base import _convert_message_to_dict
        wire_requested={c['id'] for m in messages for c in _convert_message_to_dict(m).get('tool_calls',[]) if c.get('id')}
        assert wire_requested==requested<=responded
        assert 'bad' not in wire_requested  # Invalid raw call is retained as a diagnostic, not sent again.
        assert '工具参数解析失败' in messages[-1].content and 'bad' in messages[-1].content
        qid=json.loads(next(m.content for m in results(messages) if m.tool_call_id=='good'))['question_id']
        return tool('research_schedule_experiment',{'question_id':qid,'plan':PLAN},'corrected')
    ctx,model=context(tmp_path,respond)
    assert not asyncio.run(prepare_action(ctx))
    state=ctx.research.snapshot()
    assert model.calls==2 and len(state['questions'])==len(state['experiments'])==1
    assert len([a for a in state['activity'] if a['capability']=='research.update_question'])==1
    assert any(a['capability']=='research.tool_error' and a['inputs']['calls'][0]['id']=='bad' for a in state['activity'])
