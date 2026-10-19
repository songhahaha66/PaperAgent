"""Experiment reads stay in the project; setup owns all capability configuration."""
from types import SimpleNamespace
from test_literature_api import client


def test_experiment_endpoint_requires_project_owner(client,monkeypatch):
    from routers.work_routes import workspace
    monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(created_by=8))
    assert client.get('/api/workspace/work/experiments').status_code==403


def test_old_comparison_routes_are_removed_and_experiment_reads_do_not_set_policy(client):
    initial=client.get('/api/workspace/work/experiments').json()
    assert initial['comparisons']==[] and not initial['policy']['enabled']
    assert client.get('/api/workspace/work/comparisons').status_code==404
    assert client.put('/api/workspace/work/comparisons/policy',json={'enabled':True}).status_code==404
