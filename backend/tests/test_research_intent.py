"""Offline routing contracts with fixed Choices, not classifier accuracy tests."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.judge.base import Answers, ChoiceAnswer
from ai_system.graph.agent import PaperGraphAgent
from ai_system.research.models import WorkerResult
from ai_system.research.store import ResearchStore
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


@pytest.mark.parametrize('intent,message,changes,expected', [
    ('new_research', '研究需求', {'has_research': False, 'research_id': None}, 'run'),
    ('question', '修改后的结果是什么？', {}, 'ask'),
    ('resume', '继续', {}, 'resume'),
    ('answer', '好的', {'work_status': 'waiting', 'graph_status': 'interrupted',
                      'pending_question': '是否采用 2024 年的数据？'}, 'confirm'),
    ('question', '为什么需要这个范围？', {'work_status': 'waiting', 'graph_status': 'interrupted',
                      'pending_question': '请提供数据范围'}, 'ask'),
    ('revision', '继续并把验证区间改为最后七天', {}, 'revise_research'),
])
def test_auto_uses_one_choice(intent, message, changes, expected):
    judge = FixedChoice(intent)
    state = snapshot(**changes)
    before = state.model_dump()
    assert asyncio.run(chat.resolve_action('auto', message, state, judge=judge)) == expected
    assert len(judge.calls) == 1 and state.model_dump() == before
    assert judge.calls[0][0]['message'] == message
    assert judge.calls[0][0]['pending_question'] == (state.pending_question or '')


def test_explicit_controls_never_call_jev_or_reinterpret_the_message():
    judge = FixedChoice('clarify')
    for action in ('ask', 'run', 'resume', 'revise_research', 'confirm', 'revise'):
        state = snapshot(**({'work_status': 'waiting', 'pending_question': '数据范围？'}
                            if action in {'confirm', 'revise'} else {}))
        assert asyncio.run(chat.resolve_action(action, '继续', state, judge=judge)) == action
    assert not judge.calls
    with pytest.raises(ValueError, match='等待必要信息'):
        asyncio.run(chat.resolve_action('resume', '继续', snapshot(work_status='waiting'), judge=judge))


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


def test_context_only_contains_bounded_allowlisted_fields():
    judge = FixedChoice('question')
    history = [{'role': 'assistant', 'content': 'x' * 2000,
                'metadata': {'api_key': 'must-not-send'}, 'json_blocks': [{'paper': 'must-not-send'}]}] * 10
    state = snapshot(requirements=[{'source_quote': 'must-not-send'}], runtime={'secret': 'must-not-send'})
    asyncio.run(chat.resolve_action('auto', '解释已有结果', state, history, judge=judge))
    context, _ = judge.calls[0]
    assert len(context['recent_dialogue']) == 6
    assert all(len(item['content']) <= 600 for item in context['recent_dialogue'])
    assert 'must-not-send' not in str(context)


def test_recovery_preserves_accepted_results_and_requires_explicit_operations(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path, 'study')
    source = 'Estimate demand'
    store.initialize(source, {'subproblems': [{'id': 'Q1', 'title': source, 'question': source, 'depends_on': []}],
        'requirements': [{'id': 'RQ1', 'statement': source, 'source_quote': source,
                          'kind': 'goal', 'task_ids': ['Q1'], 'checks': []}], 'assumptions': []})
    store.commit(WorkerResult(order=store.claim('Q1'), execution_status='succeeded', acceptance='accepted',
        report={'status': 'completed', 'requirement_evidence': [{'requirement_id': 'RQ1', 'evidence_refs': ['R1']}]},
        results=[{'id': 'R1', 'subproblem': 'Q1', 'name': 'demand', 'value': '8', 'run_id': 'run_offline'}]))
    agent = PaperGraphAgent(None, workspace_dir=str(tmp_path), judge=None)
    agent._save_thread('study', 'paused')
    before = store.snapshot()
    updates = []

    class Graph:
        async def aget_state(self, _):
            return SimpleNamespace(values={}, interrupts=(), next=())

        async def aupdate_state(self, *_, **kwargs):
            updates.append(kwargs)

    async def invoke(*_):
        return 'resumed'

    monkeypatch.setattr(agent, '_invoke', invoke)
    monkeypatch.setattr('ai_system.graph.agent.refresh_registry', lambda _: None)
    assert asyncio.run(agent._resume_graph(Graph(), {}, 'study', SimpleNamespace(research=store, max_planning_calls=None))) == 'resumed'
    after = store.snapshot()
    for key in ('revision', 'spec', 'experiments', 'artifacts', 'sections'):
        assert after[key] == before[key]
    assert updates == [{'as_node': 'solve_join'}]
    with pytest.raises(ValueError, match='已有研究'):
        asyncio.run(agent._prepare(Graph(), '继续'))
    assert agent._load_thread() == 'study'
    with store._transaction() as db:
        current = store._read(db)
        current['action'] = {'id': 'ask-1', 'capability': 'ask', 'status': 'waiting', 'inputs': {'message': '数据范围？'}}
        store._save(db, current, 'offline_question', {})
    pending = store.snapshot()
    assert chat.research_snapshot(tmp_path, 'work', 'waiting').pending_question == '数据范围？'
    with pytest.raises(ValueError, match='等待必要信息'):
        asyncio.run(agent._resume_graph(Graph(), {}, 'study', SimpleNamespace(research=store)))
    assert store.snapshot() == pending
