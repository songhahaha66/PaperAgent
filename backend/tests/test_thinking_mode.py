"""Connection persistence and actual request serialization, without external calls."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ai_system.core_handlers.llm_providers import create_llm_from_model_config
from ai_system.core_handlers.model_runtime import model_identity
from ai_system.graph.llm_utils import call_structured, cap_output


def connection(**kwargs):
    return SimpleNamespace(model_id='deepseek-flash', base_url='https://api.deepseek.com/v1',
                           api_key='offline-test-key', **kwargs)


def test_toggle_changes_request_and_runtime_identity_but_other_services_are_untouched():
    off=create_llm_from_model_config(connection(thinking_enabled=False))
    on=create_llm_from_model_config(connection(thinking_enabled=True))
    assert off._get_request_payload([HumanMessage('test')])['extra_body']['thinking']['type']=='disabled'
    assert on._get_request_payload([HumanMessage('test')])['extra_body']['thinking']['type']=='enabled'
    assert model_identity(off)!=model_identity(on)
    assert cap_output(on,1800).max_tokens==16000
    assert cap_output(off,1800).max_tokens==1800
    custom=connection(thinking_enabled=True);custom.base_url='https://provider.example/v1'
    assert create_llm_from_model_config(custom).extra_body is None


def test_tool_followup_keeps_reasoning_content_on_wire():
    from langchain_openai.chat_models.base import _convert_dict_to_message
    llm=create_llm_from_model_config(connection(thinking_enabled=True))
    reply=_convert_dict_to_message({'role':'assistant','content':'','reasoning_content':'offline reasoning',
        'tool_calls':[{'id':'call-one','type':'function','function':{'name':'sum_values','arguments':'{"a":2,"b":3}'}}]})
    payload=llm._get_request_payload([HumanMessage('sum'),reply,ToolMessage('5',tool_call_id='call-one')])
    assert payload['messages'][1]['reasoning_content']=='offline reasoning'
    assert payload['messages'][2]['tool_call_id']=='call-one'


def test_thinking_structured_reply_uses_existing_json_validation_without_forced_tool():
    class Answer(BaseModel):
        total:int
    class Model:
        extra_body={'thinking':{'type':'enabled'}}
        def with_structured_output(self,*args,**kwargs):
            raise AssertionError('Thinking mode must not force a named tool')
        async def ainvoke(self,messages):
            assert 'JSON Schema' in messages[-1].content
            return AIMessage(content='{"total":5}')
    assert asyncio.run(call_structured(Model(),Answer,[HumanMessage('2+3')])).total==5


def test_persist_apply_default_and_edit_keep_thinking_and_never_return_key():
    from models.models import Base, ModelConfig
    from schemas.schemas import ModelConfigCreate, ModelConfigUpdate
    from services.data_services.crud import create_model_config, update_model_config
    from routers.config_routes.model_config import apply_default_model, _remove_api_key_from_config
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as db:
        source=create_model_config(db,ModelConfigCreate(type='brain',model_id='deepseek-flash',
            base_url='https://api.deepseek.com/v1',api_key='offline-test-key',thinking_enabled=True),1)
        public=asyncio.run(apply_default_model(current_user=1,db=db))
        assert {r['type'] for r in public}=={'brain','code','writing'}
        assert all(r['thinking_enabled'] and 'api_key' not in r for r in public)
        update_model_config(db,source.id,ModelConfigUpdate(model_id='deepseek-flash'),1)
        assert source.thinking_enabled is True
        update_model_config(db,source.id,ModelConfigUpdate(thinking_enabled=False),1)
        assert _remove_api_key_from_config(source)['thinking_enabled'] is False
        assert db.query(ModelConfig).filter_by(type='code').one().thinking_enabled is True


def test_migration_preserves_existing_connections_as_nonthinking():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path=Path(__file__).parents[1]/'alembic/versions/c8f4a620d931_add_thinking_mode.py'
    spec=importlib.util.spec_from_file_location('thinking_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with create_engine('sqlite://').begin() as conn:
        conn.execute(text('CREATE TABLE model_configs(id INTEGER PRIMARY KEY, model_id TEXT)'))
        conn.execute(text("INSERT INTO model_configs VALUES(1,'deepseek-flash')"))
        module.op=Operations(MigrationContext.configure(conn));module.upgrade()
        assert conn.execute(text('SELECT model_id,thinking_enabled FROM model_configs')).one()==('deepseek-flash',0)
