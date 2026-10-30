"""Offline routing contracts with fixed Choices, not classifier accuracy tests."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.judge.base import Answers, ChoiceAnswer
from services.chat_services import research_session as chat


class FixedChoice:
    def __init__(self, intent):
        self.intent = intent
        self.calls = []

    async def ask(self, state, questions):
        self.calls.append((state, questions))
        return Answers(judge='jev:offline', choices={
            'intent': ChoiceAnswer(self.intent, {self.intent: 1}, None)})


def snapshot(**changes):
    return chat.ResearchSnapshot(work_id='work', work_status='paused', has_research=True,
        engine_version='research-v3', research_id='study', research_revision=1,
        experiments=[{'task_id': 'Q1'}]).model_copy(update=changes)


def test_explicit_controls_never_call_jev_or_reinterpret_the_message():
    judge = FixedChoice('clarify')
    for action in ('ask', 'run', 'resume', 'revise_research', 'confirm', 'revise'):
        state = snapshot(**({'work_status': 'waiting', 'pending_question': '数据范围？'}
                            if action in {'confirm', 'revise'} else {}))
        assert asyncio.run(chat.resolve_action(action, '继续', state, judge=judge)) == action
    assert not judge.calls
    with pytest.raises(ValueError, match='等待必要信息'):
        asyncio.run(chat.resolve_action('resume', '继续', snapshot(work_status='waiting'), judge=judge))


def test_direct_research_can_receive_explicit_requirements_without_experiments():
    state = snapshot(experiments=[], questions=[{'id': 'Q1', 'retired': False}])
    judge = FixedChoice('clarify')
    assert asyncio.run(chat.resolve_action('revise_research', '交齐已有结果文件', state, judge=judge)) == 'revise_research'
    assert not judge.calls
    with pytest.raises(ValueError, match='不能修订'):
        chat.validate_action('revise_research', state.model_copy(update={'research_id': None}))


def test_failures_and_ambiguous_or_inapplicable_choices_leave_state_unchanged(monkeypatch):
    state = snapshot()
    before = state.model_dump()

    class Unavailable:
        async def ask(self, *_):
            raise RuntimeError('offline failure')

    for judge, error in ((Unavailable(), '暂时不可用'), (FixedChoice('invalid'), '暂时不可用'),
                         (FixedChoice('clarify'), '请明确'), (FixedChoice('answer'), '没有明确')):
        with pytest.raises(ValueError, match=error):
            asyncio.run(chat.resolve_action('auto', '好的', state, judge=judge))
        assert state.model_dump() == before
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    with pytest.raises(ValueError, match='未配置'):
        asyncio.run(chat.resolve_action('auto', '继续', state))
    assert state.model_dump() == before
