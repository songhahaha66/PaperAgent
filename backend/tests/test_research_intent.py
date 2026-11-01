"""Transport validation defers all ordinary messages to the message graph."""
import asyncio
import pytest
from services.chat_services import research_session as chat


def snapshot(**changes):
    return chat.ResearchSnapshot(work_id='work',work_status='paused',has_research=True,
        engine_version='research-v3',research_id='study',research_revision=1).model_copy(update=changes)


def test_ordinary_messages_defer_routing_without_inferring_restarts():
    for text in ('继续','解释这个结果','把预测区间改成最后七天','重新开始是什么意思？'):
        state=snapshot(work_status='completed')
        before=state.model_dump()
        assert asyncio.run(chat.resolve_action('auto',text,state))=='auto'
        assert state.model_dump()==before


def test_first_message_and_waiting_reply_are_both_classified_before_execution():
    assert asyncio.run(chat.resolve_action('auto','分析附件',snapshot(has_research=False,research_id=None)))=='auto'
    assert asyncio.run(chat.resolve_action('auto','按模板分析',snapshot(has_research=True,research_id=None)))=='auto'
    assert asyncio.run(chat.resolve_action('auto','2025年',snapshot(work_status='waiting',graph_status='interrupted')))=='auto'


def test_only_main_model_is_required_for_chat_readiness(monkeypatch):
    from types import SimpleNamespace
    from routers.config_routes.model_config import model_readiness
    def config(self,role,user_id):
        if role!='brain':raise ValueError('not configured')
        return SimpleNamespace(api_key='offline-fixture',model_id='offline')
    monkeypatch.setattr('ai_system.config.environment.DatabaseConfigManager.get_model_config',config)
    result=asyncio.run(model_readiness(current_user=1,db=SimpleNamespace()))
    assert result['ready'] and result['research_ready'] and not result['roles']['code']


def test_explicit_controls_keep_their_meaning():
    for action in ('ask','run','resume','revise_research','confirm','revise'):
        state=snapshot(**({'work_status':'waiting'} if action in {'confirm','revise'} else {}))
        assert asyncio.run(chat.resolve_action(action,'继续',state))==action
    with pytest.raises(ValueError,match='等待必要信息'):
        asyncio.run(chat.resolve_action('resume','继续',snapshot(work_status='waiting')))
    assert asyncio.run(chat.resolve_action('auto','解释旧结果',snapshot(engine_version='old'))) == 'auto'


@pytest.mark.parametrize('message',['',' ',None,'x'*20001])
def test_invalid_message_is_rejected_before_running(message):
    with pytest.raises(ValueError,match='有效消息'):
        asyncio.run(chat.resolve_action('auto',message,snapshot()))


@pytest.mark.parametrize('has_brain',[True,False])
def test_editor_uses_main_model_when_optional_writing_model_is_missing(monkeypatch,has_brain):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from routers.work_routes.workspace import _paper_writing_model
    brain=object()
    def get_model(role,user_id):
        assert user_id==7
        if role=='brain' and has_brain:return brain
        raise ValueError('not configured')
    monkeypatch.setattr('ai_system.config.environment.setup_environment_from_db',
        lambda db:SimpleNamespace(config_manager=SimpleNamespace(get_model_config=get_model)))
    monkeypatch.setattr('ai_system.core_handlers.llm_providers.create_llm_from_model_config',lambda config:config)
    if has_brain:assert _paper_writing_model(None,7) is brain
    else:
        with pytest.raises(HTTPException) as exc:_paper_writing_model(None,7)
        assert exc.value.status_code==409
