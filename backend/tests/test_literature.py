"""Public-network isolation and literature provenance; no live services required."""
import asyncio
import json
import socket
import ssl
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import pytest

from services import literature_fetch as net
from ai_system.research import literature as lib
from ai_system.research.literature import LiteratureStore, LiteratureService


@pytest.fixture(autouse=True)
def isolated_proxy_environment(monkeypatch):
    for key in ('HTTPS_PROXY','HTTP_PROXY','https_proxy','http_proxy','ALL_PROXY','all_proxy','NO_PROXY','no_proxy'):
        monkeypatch.delenv(key,raising=False)


def seed(workspace, text='Linear programming optimizes a linear objective subject to linear equality and inequality constraints.'):
    store = LiteratureStore(workspace)
    source = store.upsert({'title':'A sourced method','doi':'10.1000/example','url':'https://doi.org/10.1000/example',
                           'authors':['Actual Author'],'year':2024,'provider':'fixture'})
    store.add_passages(source,[{'text':text,'locator':'摘要段落 1'}],'abstract',source['url'],'hash-one')
    return store,source,store.passages()[0]


@pytest.mark.parametrize('url',[
    'file:///etc/passwd','http://127.0.0.1/','http://169.254.169.254/latest/',
    'https://[::1]/','https://[::ffff:127.0.0.1]/','http://localhost/a',
    'http://foo.internal/a','https://user:pass@example.org/','https://example.org:444/a',
    'https://example.org/\nheader','https://example.org/\\x','https://example.org/?api_key=secret',
])
def test_nonpublic_or_authenticated_urls_are_rejected(url):
    with pytest.raises(net.FetchError):net.public_url(url)


@pytest.mark.parametrize('addresses', [['127.0.0.1'],['8.8.8.8','10.0.0.1'],['169.254.169.254']])
def test_dns_cannot_turn_a_public_name_into_an_internal_request(monkeypatch, addresses):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(None,None,None,None,(ip,443)) for ip in addresses])
    with pytest.raises(net.FetchError):net.fetch_public('https://public.example/',net.FetchBudget())


def test_validated_ip_is_pinned_with_tls_hostname_checks_and_no_credentials(monkeypatch):
    resolutions,connections,targets,headers = [],[],[],[]
    def resolve(host,port,**kwargs):
        resolutions.append(host)
        return [(None,None,None,None,('8.8.8.8',port))]
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    monkeypatch.setattr(socket,'create_connection',lambda target,*a:targets.append(target))
    class Connection:
        sock=None
        def __init__(self,host,port,timeout,context):
            assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
            connections.append(host)
        def request(self,method,path,headers):
            assert method=='GET';globals_headers=dict(headers)
            assert 'Authorization' not in globals_headers and 'Cookie' not in globals_headers
            self._create_connection(('public.example',443))
        def getresponse(self):
            chunks=[b'public data',b'']
            return SimpleNamespace(status=200,getheader=lambda key,default=None:default,
                                   read=lambda n:chunks.pop(0))
        def close(self):pass
    monkeypatch.setattr(net.http.client,'HTTPSConnection',Connection)
    result=net.fetch_public('https://public.example/',net.FetchBudget())
    assert result.body==b'public data'
    assert resolutions==['public.example'] and connections==['public.example']
    assert targets==[('8.8.8.8',443)]


def test_redirect_is_revalidated_before_a_second_connection(monkeypatch):
    connected=[]
    monkeypatch.setattr(net,'public_addresses',lambda host,port:['8.8.8.8'])
    class Connection:
        def __init__(self,host,*a,**k):connected.append(host)
        def request(self,*a,**k):pass
        def getresponse(self):return SimpleNamespace(status=302,getheader=lambda k,default=None:'http://127.0.0.1/private' if k=='Location' else default)
        def close(self):pass
    monkeypatch.setattr(net.http.client,'HTTPSConnection',Connection)
    with pytest.raises(net.FetchError):net.fetch_public('https://public.example/',net.FetchBudget())
    assert connected==['public.example']


def test_server_proxy_tunnels_to_validated_ip_without_source_credentials(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY','http://proxy.internal:8080')
    observed={};socket_object=object()
    class Tunnel:
        def __init__(self,host,port,timeout):observed.update(proxy_host=host,proxy_port=port);self.sock=socket_object
        def set_tunnel(self,host,port,headers):observed.update(target=(host,port),headers=headers)
        def connect(self):pass
        def close(self):pass
    monkeypatch.setattr(net.http.client,'HTTPConnection',Tunnel)
    assert net.dial_public('8.8.8.8',443,'public.example','https',2) is socket_object
    assert observed['target']==('8.8.8.8',443)
    assert observed['headers']=={'Host':'public.example'}


def test_response_size_and_request_budget_are_enforced(monkeypatch):
    monkeypatch.setattr(net,'public_addresses',lambda *a:['8.8.8.8'])
    class Connection:
        sock=None
        def __init__(self,*a,**k):pass
        def request(self,*a,**k):pass
        def getresponse(self):return SimpleNamespace(status=200,getheader=lambda k,default=None:'99999' if k=='Content-Length' else default,
                                                     read=lambda n:pytest.fail('Oversized response must not be read'))
        def close(self):pass
    monkeypatch.setattr(net.http.client,'HTTPSConnection',Connection)
    with pytest.raises(net.FetchError,match='大小'):net.fetch_public('https://public.example/',net.FetchBudget(),100)
    with pytest.raises(net.FetchError,match='请求预算'):net.fetch_public('https://public.example/',net.FetchBudget(max_requests=0))


def test_metadata_dedup_and_passage_versions_do_not_change_experimental_results(tmp_path):
    store,source,passage=seed(tmp_path)
    same=store.upsert({'url':'https://publisher.example/paper#section','doi':'https://doi.org/10.1000/EXAMPLE','title':'New metadata title'})
    assert same['id']==source['id'] and len(store.state()['sources'])==1
    store.add_passages(same,[{'text':passage['text'],'locator':'摘要段落 1'}],'abstract',same['url'],'hash-one')
    assert len(store.passages())==1
    store.add_passages(same,[{'text':passage['text']+' An updated source version.','locator':'摘要段落 1'}],'abstract',same['url'],'hash-two')
    assert len(store.passages())==2
    assert store.evidence(passage['id'])['source']['title']=='A sourced method'
    assert store.research.snapshot() is None
    assert store.research.registry_payload()['results']==[]
    assert store.retrieve('linear objective')[0]['boundary']=='abstract'


def test_doi_and_resolved_url_collision_merges_without_losing_citation_ids(tmp_path):
    store,source,original=seed(tmp_path)
    other=store.upsert({'url':'https://publisher.example/article','title':'Public page','doi':None})
    store.add_passages(other,[{'text':'A second accessible source paragraph contains the same linear programming method discussion.'}],
                       'webpage',other['url'],'second-hash')
    second=store.passages(other['id'])[0]
    merged=store.upsert({'url':other['url'],'doi':source['doi'],'title':source['title']})
    assert merged['id']==source['id'] and len(store.state()['sources'])==1
    assert store.source(other['id'])['id']==source['id']
    assert store.evidence(original['id'])['text']==original['text']
    assert store.evidence(second['id'])['text']==second['text']
    assert len(store.passages(source['id']))==2


def test_search_merges_dois_and_only_oa_xml_is_full_text(tmp_path,monkeypatch):
    xml=b'<?xml version="1.0"?><!DOCTYPE article SYSTEM "https://example.test/not-fetched.dtd"><article><front><article-title>Method</article-title><license>CC BY</license></front><body><sec><p id="p1">Linear programming optimizes an objective under explicit linear constraints and permits a reproducible formulation.</p></sec></body></article>'
    calls=[]
    async def fetch(url,budget,max_bytes=0):
        budget.take();calls.append(url)
        if 'crossref' in url:
            data={'message':{'items':[{'DOI':'10.1000/example','title':['Method'],'author':[], 'abstract':'<p>An abstract describes a method but does not supply all validation details from the paper.</p>'}]}}
            return net.PublicDocument(url,json.dumps(data).encode(),'application/json')
        if 'fullTextXML' in url:return net.PublicDocument(url,xml,'application/xml')
        data={'resultList':{'result':[{'doi':'10.1000/EXAMPLE','title':'Method','pmcid':'PMC123','isOpenAccess':'Y','abstractText':'An independently supplied abstract describes the method and its intended application.'}]}}
        return net.PublicDocument(url,json.dumps(data).encode(),'application/json')
    monkeypatch.setattr(lib,'fetch_document',fetch)
    service=LiteratureService(tmp_path)
    result=asyncio.run(service.search('linear programming'))
    assert len(result['sources'])==1 and len(calls)==3
    assert result['sources'][0]['status']=='fulltext_xml_available'
    rows=service.store.passages()
    assert {r['boundary'] for r in rows}=={'abstract','fulltext_xml'}
    body=next(r for r in rows if r['boundary']=='fulltext_xml')
    assert 'XML id=p1' in body['locator'] and body['page'] is None
    assert body['license']=='CC BY'


def test_entities_and_failed_full_text_never_become_evidence(tmp_path,monkeypatch):
    service=LiteratureService(tmp_path)
    source=service.store.upsert({'url':'https://public.example/article','title':'Article','doi':None,'open_access':True,'fulltext_url':'https://public.example/full.xml'})
    async def fetch(url,*args):
        return net.PublicDocument(url,b'<!DOCTYPE article [<!ENTITY steal SYSTEM "file:///etc/passwd">]><article><body><p>&steal;</p></body></article>','application/xml')
    monkeypatch.setattr(lib,'fetch_document',fetch)
    result=asyncio.run(service.fetch_source(source['id']))
    assert result['operation']['status']=='failed' and not result['evidence']
    assert '安全解析' in result['error']


def test_operation_budget_is_persisted(tmp_path):
    store=LiteratureStore(tmp_path)
    for _ in range(20):store.operation('search','topic')
    with pytest.raises(ValueError,match='预算'):store.operation('search','one more')


def test_explicit_research_search_is_policy_controlled_and_resume_cached(tmp_path,monkeypatch):
    calls=[]
    async def search(self,query,limit):
        calls.append(query)
        oid,record=self.store.operation('search',query)
        self.store.finish(oid,{**record,'status':'completed'})
        return {'search':'completed'}
    monkeypatch.setattr(lib.LiteratureService,'search',search)
    asyncio.run(lib.maybe_search(tmp_path,'仅求解这个问题','LP task'))
    assert not calls
    asyncio.run(lib.maybe_search(tmp_path,'先检索相关文献','LP task'))
    asyncio.run(lib.maybe_search(tmp_path,'先检索相关文献','LP task'))
    assert calls==['LP task']
    lib.LiteratureStore(tmp_path).set_policy('off')
    asyncio.run(lib.maybe_search(tmp_path,'先检索相关文献','another task'))
    assert len(calls)==1


def test_pdf_extraction_runs_isolated_and_retains_real_page(tmp_path):
    import subprocess
    # A small real PDF, with a text stream and one page; no model or network.
    text=b'BT /F1 12 Tf 36 720 Td (Linear programming has a linear objective and explicit constraints.) Tj ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(text)).encode()+b' >>\nstream\n'+text+b'\nendstream']
    data=b'%PDF-1.4\n';offsets=[]
    for i,obj in enumerate(objects,1):
        offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(data);data+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    data+=b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF'
    result=subprocess.run([sys.executable,str(Path(lib.__file__).resolve().parents[2]/'services/literature_pdf.py')],
        input=data,capture_output=True,timeout=8,env={'PATH':'/usr/bin:/bin','PYTHONIOENCODING':'utf-8'},check=True)
    rows=json.loads(result.stdout)['paragraphs']
    assert rows[0]['page']==1 and 'Linear programming' in rows[0]['text']
