import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers.work_routes import workspace
from database.database import get_db
from auth.auth import get_current_user
from ai_system.research import literature as lib
from services.literature_fetch import PublicDocument


@pytest.fixture
def client(tmp_path,monkeypatch):
    app=FastAPI();app.include_router(workspace.router)
    app.dependency_overrides[get_current_user]=lambda:7
    app.dependency_overrides[get_db]=lambda:SimpleNamespace(close=lambda:None)
    monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(architecture_version=2, created_by=7))
    monkeypatch.setattr(workspace.workspace_file_service,'get_workspace_path',lambda _:tmp_path)
    with TestClient(app) as c:yield c


@pytest.mark.parametrize('method,path,body',[
    ('GET','',None),('POST','/search',{'query':'linear programming'}),
    ('POST','/import',{'url':'https://example.org/paper'}),('GET','/sources/L1',None),
    ('POST','/sources/L1/fetch',None),('GET','/evidence/C1',None),
    ('POST','/retrieve',{'query':'method'}),('PUT','/policy',{'mode':'auto'}),
    ('POST','/evidence/C1/check',{'claim':'This claim needs evidence support'}),
])
def test_every_literature_route_checks_work_ownership(client,monkeypatch,method,path,body):
    monkeypatch.setattr(workspace.crud,'get_work',lambda *a:SimpleNamespace(architecture_version=2, created_by=8))
    assert client.request(method,'/api/workspace/work/literature'+path,json=body).status_code==403


def test_search_detail_retrieval_and_failure_are_explicit(client,monkeypatch):
    async def fetch(url,budget,max_bytes=0):
        budget.take()
        data=({'message':{'items':[{'DOI':'10.1000/example','title':['Actual method'],
            'abstract':'<p>This source discusses linear programming and the explicit construction of linear constraints.</p>'}]}}
            if 'crossref' in url else {'resultList':{'result':[]}})
        return PublicDocument(url,json.dumps(data).encode(),'application/json')
    monkeypatch.setattr(lib,'fetch_document',fetch)
    base='/api/workspace/work/literature'
    assert client.get(base).json()['sources']==[]
    result=client.post(base+'/search',json={'query':'linear programming'}).json()
    assert len(result['sources'])==1
    assert result['sources'][0]['status']=='abstract_available'
    detail=client.get(base+'/sources/L1').json()
    assert detail['evidence'][0]['boundary']=='abstract' and detail['evidence'][0]['page'] is None
    found=client.post(base+'/retrieve',json={'query':'linear programming'}).json()['evidence']
    assert len(found)==1
    assert client.get(base+'/evidence/'+found[0]['id']).json()['source']['title']=='Actual method'
    assert client.post(base+'/import',json={'url':'http://127.0.0.1/secret'}).status_code==409
    assert client.get(base+'/evidence/C9999').status_code==404
    assert client.put(base+'/policy',json={'mode':'auto'}).json()['mode']=='auto'
