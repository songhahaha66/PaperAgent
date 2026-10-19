import json
from types import SimpleNamespace
import pytest
from test_literature_api import client
from ai_system.research.comparison import SupervisedSpec


@pytest.mark.parametrize('method,path,body',[('GET','',None),('PUT','/policy',{'enabled':True})])
def test_comparison_endpoints_require_work_owner(client,monkeypatch,method,path,body):
    from routers.work_routes import workspace
    monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(created_by=8))
    assert client.request(method,'/api/workspace/work/comparisons'+path,json=body).status_code==403


def test_policy_is_validated_and_cannot_change_while_research_running(client,monkeypatch):
    from routers.work_routes import workspace
    base='/api/workspace/work/comparisons'
    initial=client.get(base).json();assert initial['comparisons']==[] and not initial['policy']['enabled']
    spec=SupervisedSpec(dataset='data/source.csv',target='y',features=['x']).model_dump()
    changed=client.put(base+'/policy',json={'enabled':True,'spec':spec}).json()
    assert changed['version']==1 and changed['enabled']
    invalid=client.put(base+'/policy',json={'enabled':True,'spec':{**spec,'features':['y']}})
    assert invalid.status_code==422 and client.get(base).json()['policy']['version']==1
    monkeypatch.setattr(workspace.task_manager,'get_running_task',lambda _:object())
    assert client.put(base+'/policy',json={'enabled':False}).status_code==409
    assert client.get(base).json()['policy']['enabled']
