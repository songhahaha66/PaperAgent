"""Visible model settings must reach execution and enforce known capacity."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from ai_system.config.generation import model_capabilities
from ai_system.core_handlers.llm_providers import create_llm_from_model_config


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
    from ai_system.graph.images import vision_policy
    config = SimpleNamespace(model_id='deepseek-flash', base_url='https://api.deepseek.com/v1',
                             api_key='offline', generation_settings={})
    native = create_llm_from_model_config(config)
    assert vision_policy(native).enabled and vision_policy(native).tokens(1600,1600) == 1024
    config.model_id='custom-vision';config.base_url='https://gateway.invalid/v1'
    config.generation_settings={'image_inputs':True,'image_tokens_per_image':4096}
    custom = create_llm_from_model_config(config)
    assert vision_policy(custom).enabled and vision_policy(custom).tokens(1600,1600) == 4096
    config.generation_settings={'image_inputs':False}
    assert not vision_policy(create_llm_from_model_config(config)).enabled


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
