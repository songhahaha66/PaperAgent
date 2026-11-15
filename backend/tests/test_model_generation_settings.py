"""Visible model settings must reach execution and enforce known capacity."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from ai_system.config.generation import model_capabilities
from ai_system.core_handlers.llm_providers import create_llm_from_model_config


@pytest.mark.parametrize('model_id', ['gpt-6.1-sol', 'gpt-6.1-terra'])
def test_official_gpt6_tool_round_trip_uses_responses_without_temperature(monkeypatch, model_id):
    import json
    from langchain_core.messages import HumanMessage, ToolMessage
    from ai_system.core_handlers import llm_providers

    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.path == '/v1/responses'
        assert 'temperature' not in payload
        if len(requests) == 1:
            assert payload['tools'][0]['name'] == 'check_connection'
            output = [{'type': 'function_call', 'id': 'fc_check', 'call_id': 'call_check',
                       'name': 'check_connection', 'arguments': '{}', 'status': 'completed'}]
        else:
            assert any(item.get('type') == 'function_call_output' and item['output'] == 'OK'
                       for item in payload['input'])
            output = [{'type': 'message', 'id': 'msg_check', 'role': 'assistant', 'status': 'completed',
                       'content': [{'type': 'output_text', 'text': 'OK', 'annotations': []}]}]
        return httpx.Response(200, json={'id': f'resp_{len(requests)}', 'object': 'response',
            'created_at': 1, 'status': 'completed', 'model': model_id, 'output': output,
            'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}})

    client_class = httpx.Client
    with client_class(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(llm_providers, 'httpx', SimpleNamespace(
            Client=lambda **kwargs: client, AsyncClient=httpx.AsyncClient))
        model = create_llm_from_model_config(SimpleNamespace(model_id=model_id,
            base_url='https://api.openai.com/v1', api_key='offline',
            generation_settings={'reasoning_effort': 'medium'}), streaming=False)
        bound = model.bind_tools([{'name': 'check_connection', 'description': 'Check the connection',
                                  'parameters': {'type': 'object', 'properties': {}}}])
        prompt = HumanMessage(content='Check the connection.')
        first = bound.invoke([prompt])
        assert first.tool_calls[0]['name'] == 'check_connection'
        final = bound.invoke([prompt, first, ToolMessage(content='OK', tool_call_id='call_check')])
        assert final.text == 'OK'
        assert final.usage_metadata['total_tokens'] == 15
    assert len(requests) == 2


def test_official_deepseek_uses_known_window_and_explicit_selection_wins():
    config = SimpleNamespace(model_id='deepseek-flash', base_url='https://api.deepseek.com/v1',
                             api_key='offline', thinking_enabled=True, generation_settings={})
    model = create_llm_from_model_config(config)
    assert model.profile['max_context_tokens'] == 1000000
    assert model.profile['max_input_tokens'] == 984000
    config.generation_settings = {'context_window': 512000, 'reasoning_effort': 'low'}
    selected = create_llm_from_model_config(config)
    assert selected.profile['max_context_tokens'] == 512000
    assert selected.profile['max_input_tokens'] == 496000
    assert selected.reasoning_effort == 'low' and selected.extra_body['thinking']['type'] == 'enabled'
    config.thinking_enabled = False
    config.generation_settings = {'context_window': 512000}
    config.extra_body = {'thinking': {'type': 'enabled'}, 'custom_option': 'retained'}
    disabled = create_llm_from_model_config(config)
    assert disabled.extra_body['thinking']['type'] == 'disabled' and disabled.reasoning_effort is None
    assert disabled.extra_body['custom_option'] == 'retained'


def test_unknown_gateway_capacity_is_not_inferred_from_the_endpoint_name():
    assert model_capabilities('unknown-gateway-model')['max_context_tokens'] is None
    config = SimpleNamespace(model_id='unknown-gateway-model', base_url='https://gateway.invalid/v1',
                             api_key='offline', generation_settings={'context_window': 256000})
    model = create_llm_from_model_config(config)
    assert model.profile['max_context_tokens'] == 256000
    config.model_id = 'gpt-4o'
    with pytest.raises(ValueError, match='128000'):
        create_llm_from_model_config(config)


def test_vision_settings_reach_the_actual_model_projection_without_network_calls():
    from ai_system.config.generation import GenerationSettings
    from ai_system.agent.images import vision_policy
    config = SimpleNamespace(model_id='deepseek-flash', base_url='https://api.deepseek.com/v1',
                             api_key='offline', generation_settings={})
    native = create_llm_from_model_config(config)
    assert vision_policy(native).enabled and vision_policy(native).strategy=='deepseek'
    assert vision_policy(native).tokens(1600,1600) == 3000
    config.model_id='gpt-6.1-terra';config.base_url='https://gateway.invalid/v1'
    config.generation_settings={'image_tokens_per_image':4096}
    terra = create_llm_from_model_config(config)
    assert vision_policy(terra).enabled and vision_policy(terra).strategy=='inline'
    assert vision_policy(terra).tokens(1600,1600) == 3000
    assert terra.metadata['generation_settings']['image_inputs'] is True
    assert 'image_tokens_per_image' not in terra.metadata['generation_settings']
    loaded=GenerationSettings.model_validate({'image_inputs':None,'image_tokens_per_image':2048,'context_window':128000})
    assert loaded.image_inputs is True and loaded.context_window==128000
    config.generation_settings={'image_inputs':False}
    assert not vision_policy(create_llm_from_model_config(config)).enabled


def test_env_image_inputs_default_on_and_can_be_disabled(monkeypatch):
    from ai_system.config.api_settings import EnvModelConfig, load_env_api_settings
    monkeypatch.setenv('PAPERAGENT_API_KEY', 'offline')
    monkeypatch.setenv('PAPERAGENT_API_BASE', 'http://gateway.example:30080')
    monkeypatch.delenv('PAPERAGENT_API_IMAGE_INPUTS', raising=False)
    settings = load_env_api_settings()
    assert settings.image_inputs is True
    model = create_llm_from_model_config(EnvModelConfig(settings))
    from ai_system.agent.images import vision_policy
    assert vision_policy(model).enabled and vision_policy(model).strategy == 'inline'
    monkeypatch.setenv('PAPERAGENT_API_IMAGE_INPUTS', 'off')
    assert load_env_api_settings().image_inputs is False
    monkeypatch.setenv('PAPERAGENT_API_IMAGE_INPUTS', 'maybe')
    with pytest.raises(ValueError, match='PAPERAGENT_API_IMAGE_INPUTS'):
        load_env_api_settings()


def test_auto_disable_persists_on_the_saved_model_config(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models.models import Base, ModelConfig, User
    from ai_system.config.generation import disable_saved_image_inputs
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(User(username='owner', email='owner@example.com', password_hash='x'))
        db.commit()
        user_id = db.query(User).one().id
        db.add(ModelConfig(type='brain', provider='openai', model_id='gpt-6.1-terra',
                            base_url='https://gateway.example/v1', api_key='offline', created_by=user_id,
                            generation_settings={'context_window': 128000, 'image_inputs': True, 'image_tokens_per_image': 4096}))
        db.commit()
        config_id = db.query(ModelConfig).one().id
    monkeypatch.setenv('DATABASE_URL', 'sqlite://')
    monkeypatch.setattr('database.database.SessionLocal', Session)
    assert disable_saved_image_inputs(config_id) is True
    with Session() as db:
        saved = db.get(ModelConfig, config_id).generation_settings
    assert saved['image_inputs'] is False and saved['context_window'] == 128000
    assert 'image_tokens_per_image' not in saved
    assert disable_saved_image_inputs(None) is False


def test_capacity_lookup_and_save_validation_through_real_routes():
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from models.models import Base
    from routers.config_routes.model_config import router
    from auth.auth import get_current_user
    from database.database import get_db
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = FastAPI();app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: 1
    def database():
        with Session(engine) as db:yield db
    app.dependency_overrides[get_db] = database
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            caps = (await client.get('/model-configs/capabilities', params={'model_id': 'gpt-4o'})).json()
            assert caps['max_context_tokens'] == 128000 and caps['reasoning_efforts'] == []
            created = await client.post('/model-configs', json={
                'type': 'brain', 'model_id': 'deepseek-flash', 'base_url': 'https://api.deepseek.com/v1',
                'api_key': 'offline', 'thinking_enabled': True,
                'generation_settings': {'context_window': 1000000, 'reasoning_effort': 'high'}})
            assert created.status_code == 200, created.text
            saved = created.json();assert 'api_key' not in saved
            rejected = await client.put(f"/model-configs/{saved['id']}", json={'model_id': 'gpt-4o'})
            assert rejected.status_code == 400 and '128000' in rejected.json()['detail']
            retained = (await client.get(f"/model-configs/{saved['id']}")).json()
            assert retained['model_id'] == 'deepseek-flash' and retained['generation_settings']['context_window'] == 1000000
            updated = await client.put(f"/model-configs/{saved['id']}", json={
                'model_id': 'gpt-4o', 'generation_settings': {'context_window': 128000}})
            assert updated.status_code == 200 and updated.json()['generation_settings']['context_window'] == 128000
    try:asyncio.run(run())
    finally:engine.dispose()
