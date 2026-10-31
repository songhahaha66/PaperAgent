"""Public-network isolation and literature provenance; no live services required."""
import socket
import ssl
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import pytest

from services import literature_fetch as net
from helpers.literature import seed


@pytest.fixture(autouse=True)
def isolated_proxy_environment(monkeypatch):
    for key in ('HTTPS_PROXY','HTTP_PROXY','https_proxy','http_proxy','ALL_PROXY','all_proxy','NO_PROXY','no_proxy'):
        monkeypatch.delenv(key,raising=False)


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
