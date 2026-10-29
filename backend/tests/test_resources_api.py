from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.encoders import jsonable_encoder
from auth.auth import get_current_user
from routers.resource_routes import router
from ai_system.modules.methods import skill_catalog, skill_detail


def test_resources_are_authenticated_and_do_not_require_a_project():
    app=FastAPI();app.include_router(router)
    with TestClient(app) as client:
        assert client.get('/api/resources/methods').status_code==401
        app.dependency_overrides[get_current_user]=lambda:1
        assert client.get('/api/resources/methods').json()['methods']==jsonable_encoder(skill_catalog())
        assert client.get('/api/resources/methods/arima').json()==jsonable_encoder(skill_detail('arima'))
        assert client.get('/api/resources/methods/not-a-method').status_code==404
