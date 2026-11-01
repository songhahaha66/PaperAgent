"""Real message graph with structured offline models and durable chat turns."""
import asyncio
import json

import pytest
from langchain_core.messages import HumanMessage

from ai_system.graph.message_graph import (
    MessageIntent, MessageContext, build_message_graph, resolve_intent,
)
from services.chat_services.chat_history_manager import ChatHistoryManager
from services.chat_services.research_session import ResearchSnapshot
from research_tools_helpers import ResearchToolsModel, tool


def snapshot(**values):
    return ResearchSnapshot(work_id='work', work_status='failed', research_id='study',
        engine_version='research-v3', research_revision=2,
        questions=[{'id': 'Q1', 'title': '预测范围', 'experiment_ids': []}],
        **values)


@pytest.mark.parametrize('intent,status,expected', [
    ('answer', 'failed', 'ask'), ('answer', 'waiting', 'ask'),
    ('work', 'completed', 'message'), ('amend', 'completed', 'revise_research'),
    ('amend', 'waiting', 'revise_research'), ('respond', 'waiting', 'confirm'),
    ('respond', 'completed', 'clarify'), ('resume', 'failed', 'resume'),
    ('resume', 'waiting', 'clarify'), ('resume', 'completed', 'clarify'),
    ('clarify', 'completed', 'clarify'),
])
def test_graph_routes_from_structured_model_and_commits_before_dispatch(tmp_path, intent, status, expected):
    root = tmp_path / 'work'; root.mkdir()
    history = ChatHistoryManager(tmp_path)
    turn = history.begin_turn('work', 'user:one', '先解释，再按2025年重算')
    captured = []
    state = snapshot().model_copy(update={'work_status': status, 'pending_question': '使用哪一年？' if status == 'waiting' else None})
    def respond(messages):
        prompt = json.loads(next(m.content for m in messages if isinstance(m, HumanMessage)))
        assert prompt['history'][0]['content'] == '2025年'
        assert prompt['state']['pending_question'] == state.pending_question
        return tool('MessageIntent', {'intent': intent, 'question_id': 'Q1',
                    'clarification': '你希望先解释还是立即执行？' if intent == 'clarify' else ''})
    model = ResearchToolsModel(respond=respond)
    async def execute(route):
        persisted = history.get_messages('work')[0]['metadata']['routing']
        assert persisted == route
        captured.append(route)
    ctx = MessageContext(model, root, state, turn['content'], [{'role':'user','content':'2025年'}],
                         None, 'auto', history, 'work', turn, execute)
    asyncio.run(build_message_graph().ainvoke({}, context=ctx))
    assert model.calls == 1 and captured[0]['action'] == expected
    assert captured[0]['scope'] == 'Q1'
    assert captured[0]['base_revision'] == 2


def test_failed_dispatch_reuses_saved_classification_without_another_model_call(tmp_path):
    root = tmp_path / 'work'; root.mkdir()
    history = ChatHistoryManager(tmp_path)
    turn = history.begin_turn('work', 'user:retry', '解释已有结果')
    model = ResearchToolsModel(respond=lambda _: tool('MessageIntent', {'intent': 'answer'}))
    attempts=[]
    async def execute(route):
        attempts.append(route)
        if len(attempts) == 1:
            raise ConnectionError('controlled response failure')
    def context(turn):
        return MessageContext(model, root, snapshot(), turn['content'], [], None,
                              'auto', history, 'work', turn, execute)
    with pytest.raises(ConnectionError):
        asyncio.run(build_message_graph().ainvoke({}, context=context(turn)))
    recovered = history.begin_turn('work', 'user:retry', '解释已有结果')
    asyncio.run(build_message_graph().ainvoke({}, context=context(recovered)))
    assert model.calls == 1 and attempts[0] == attempts[1]
    assert len(history.get_messages('work')) == 1
    history.update_turn('work', turn['id'], turn_status='completed')
    with pytest.raises(ValueError, match='已处理'):
        history.begin_turn('work', turn['id'], turn['content'])
    with pytest.raises(ValueError, match='其他内容'):
        history.begin_turn('work', turn['id'], 'different request')


def test_initial_message_is_classified_and_no_research_is_not_an_automatic_start(tmp_path):
    empty = ResearchSnapshot(work_id='work', work_status='created')
    assert resolve_intent(MessageIntent(intent='answer'), empty) == ('ask', None)
    assert resolve_intent(MessageIntent(intent='work'), empty) == ('run', None)
    assert resolve_intent(MessageIntent(intent='resume'), empty) == ('clarify', None)


def test_unknown_question_or_invalid_schema_cannot_dispatch(tmp_path):
    with pytest.raises(ValueError, match='范围'):
        resolve_intent(MessageIntent(intent='work', question_id='Q99'), snapshot())
    with pytest.raises(ValueError):
        MessageIntent(intent='delete_everything')
    with pytest.raises(ValueError, match='澄清'):
        MessageIntent(intent='clarify')


def test_bad_classification_never_falls_back_to_execution(tmp_path):
    root=tmp_path/'work';root.mkdir()
    history=ChatHistoryManager(tmp_path)
    turn=history.begin_turn('work','user:bad','先别继续，解释一下')
    model=ResearchToolsModel(respond=lambda _: tool('MessageIntent', {'intent':'invalid'}))
    async def execute(route):
        pytest.fail('invalid classification must not execute')
    ctx=MessageContext(model,root,snapshot(),turn['content'],[],None,'auto',history,'work',turn,execute)
    with pytest.raises(ValueError):
        asyncio.run(build_message_graph().ainvoke({},context=ctx))
    assert 'routing' not in history.get_messages('work')[0]['metadata']
