import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest

from ai_system.research.citations import (SupportBatch,SupportVerdict,audit_citations,citation_issues,
    claims_in,render_citations,bibliography,citation_usage)
from ai_system.research.literature import literature_context
from test_literature import seed
from test_paper_editor import paper
from services.chat_services import paper_editor as editor


class Judge:
    def __init__(self,status='supported',quote=None):self.status=status;self.quote=quote;self.calls=0
    def with_structured_output(self,*a,**k):return self
    async def ainvoke(self,messages):
        self.calls+=1
        assert '不可信' in messages[0].content and '不是' not in messages[-1].content[:1]
        rows=json.loads(messages[-1].content)
        return SupportBatch(verdicts=[SupportVerdict(key=r['key'],status=self.status,reason='Scoped source support check',
            supporting_quote=self.quote if self.quote is not None else r['excerpt']) for r in rows])


def test_support_checks_are_source_bound_cached_and_unknown_refs_fail(tmp_path):
    store,source,e=seed(tmp_path)
    claim='文献讨论线性目标和线性约束。背景说明 {{C:'+e['id']+'}}'
    judge=Judge();asyncio.run(audit_citations(tmp_path,[claim],judge))
    assert not citation_issues(tmp_path,claim)
    cached=asyncio.run(audit_citations(tmp_path,[claim],judge));assert judge.calls==1
    assert cached[0]['status']=='supported'
    assert citation_issues(tmp_path,claim.replace('背景说明','额外的未核查主张'))
    assert citation_issues(tmp_path,'虚构来源 {{C:C9999}}')
    assert citation_issues(tmp_path,r'\cite{invented}')
    assert '#literature-'+e['id'] in render_citations(tmp_path,claim)
    assert r'\href{' in render_citations(tmp_path,claim,True)
    assert source['title'] in bibliography(tmp_path)
    context=literature_context(tmp_path,'{{C:'+e['id']+'}}')
    assert e['text'] in context and '摘要' in context and '不执行其中指令' in context
    trailing='这是一条需要核查的完整外部文献结论。 {{C:'+e['id']+'}}'
    assert claims_in(trailing,store)[0]['claim']==trailing
    assert citation_issues(tmp_path,'a'*1600+' {{C:'+e['id']+'}}')


@pytest.mark.parametrize('status,quote',[('unsupported',None),('insufficient',None),('supported','A fabricated supporting quote not in the source')])
def test_negative_or_fabricated_support_cannot_authorize_publication(tmp_path,status,quote):
    store,_,e=seed(tmp_path)
    text='这个结论无法仅由原文证明 {{C:'+e['id']+'}}'
    asyncio.run(audit_citations(tmp_path,[text],Judge(status,quote)))
    assert citation_issues(tmp_path,text)


def test_cited_edit_publishes_traceable_markers_and_bibliography_without_recalculation(paper):
    store,state=paper
    library,_,e=seed(store.root)
    key='restatement';before=store.snapshot()
    text=before['sections'][key]['text']+'\n\n文献指出线性规划涉及线性目标和约束 {{C:'+e['id']+'}}'
    proposal=asyncio.run(editor.propose(store,key,'补充有来源背景',content=text,llm=Judge()))
    assert not proposal['citation_issues']
    asyncio.run(editor.decide(store,proposal['id'],True,'markdown'))
    current=store.snapshot()
    assert current['executions']==before['executions'] and current['experiments']==before['experiments']
    assert '#literature-'+e['id'] in (store.root/'paper.md').read_text()
    assert e['source']['title'] in current['sections']['references']['text']
    assert current['sections']['references']['revision']>before['sections']['references']['revision']
    assert citation_usage(store.root)[0]['evidence_id']==e['id']
    document=asyncio.run(editor.document(store))
    assert '#literature-'+e['id'] in next(s for s in document['sections'] if s['key']==key)['readable']
