"""Real Agent tool loop with controlled replies; no external API validation claim."""
from services.file_services.workspace_layout import workspace_path
import asyncio
import json
import pytest
from langchain_core.messages import AIMessage,ToolMessage
from ai_system.graph.context import GraphContext
from ai_system.modules.registry import invoke
from ai_system.research.loop import prepare_action,resume
from ai_system.research.runtime import execution_scope,ResearchRuntime,ModelServiceError
from ai_system.research.store import ResearchStore
from research_tools_helpers import ResearchToolsModel, tool

PLAN={'conditions':'entire supplied source','evaluation':'retain original record identity',
      'candidates':[{'name':'source count','instruction':'Count supplied observations'}]}


pytestmark = pytest.mark.usefixtures("offline_kernel")

def context(tmp_path,respond):
    model=ResearchToolsModel(respond=respond)
    ctx=GraphContext.create(str(tmp_path),planner_llm=model)
    ctx.research=ResearchStore(tmp_path,'tools');ctx.research.initialize('Count source observations and describe the selected subjects.')
    return ctx,model


def results(messages):return [m for m in messages if isinstance(m,ToolMessage)]


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
    transcript=next((workspace_path(tmp_path, '.system/transcripts')).glob('*.json')).read_text()
    assert 'fixed' in transcript and 'bad' in transcript


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


def test_user_resume_consumes_pause_and_runs_a_new_decision(tmp_path):
    from ai_system.graph.agent import PaperGraphAgent
    from services.chat_services.research_session import research_snapshot, validate_action
    def respond(messages):
        if model.calls==1:return tool('research_pause',{'message':'A required source is unavailable'},'pause')
        return tool('research_ask_user',{'message':'Please provide the source records.'},'ask')
    model=ResearchToolsModel(respond=respond)
    agent=PaperGraphAgent(model,workspace_dir=str(tmp_path))
    async def run():
        assert '研究已暂停' in await agent.run('Count the source observations.')
        store=ResearchStore(tmp_path,agent._load_thread())
        assert store.snapshot()['outcome']=='blocked'
        assert validate_action('resume',research_snapshot(tmp_path,'work','paused'))=='resume'
        assert await agent.resume_failed()=='Please provide the source records.'
        assert model.calls==2
        assert store.snapshot()['action']['status']=='waiting'
        assert not (tmp_path/'paper.md').exists()
    asyncio.run(run())


def test_planning_limit_after_user_answer_stays_paused_on_resume(tmp_path):
    from ai_system.graph.agent import PaperGraphAgent
    model=ResearchToolsModel(respond=lambda messages:tool('research_ask_user',{'message':'Which records?'},'ask'))
    agent=PaperGraphAgent(model,workspace_dir=str(tmp_path),max_planning_calls=1)
    async def run():
        assert await agent.run('Count the source observations.')=='Which records?'
        for output in (await agent.run('Use the supplied three records.',action='answer'),
                       await agent.resume_failed()):
            assert '研究已暂停' in output and '规划上限' in output
            assert '已写入' not in output
        state=ResearchStore(tmp_path,agent._load_thread()).snapshot()
        assert state['outcome']=='blocked' and state['planning_calls']==model.calls==1
        assert not (tmp_path/'paper.md').exists()
    asyncio.run(run())
