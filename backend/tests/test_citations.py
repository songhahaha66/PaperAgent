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
    assert current['attempts']==before['attempts'] and current['tasks']==before['tasks']
    assert '#literature-'+e['id'] in (store.root/'paper.md').read_text()
    assert e['source']['title'] in current['sections']['references']['text']
    assert current['sections']['references']['revision']>before['sections']['references']['revision']
    assert citation_usage(store.root)[0]['evidence_id']==e['id']
    document=asyncio.run(editor.document(store))
    assert '#literature-'+e['id'] in next(s for s in document['sections'] if s['key']==key)['readable']


def test_rejected_citation_candidate_leaves_published_paper_untouched(paper):
    store,_=paper
    _,_,e=seed(store.root)
    original=(store.root/'paper.md').read_bytes()
    text=store.snapshot()['sections']['restatement']['text']+'\n\n本研究实验已证明普适有效 {{C:'+e['id']+'}}'
    proposal=asyncio.run(editor.propose(store,'restatement','new claim',content=text,llm=Judge('unsupported')))
    assert proposal['citation_issues']
    with pytest.raises(ValueError,match='未获支持'):asyncio.run(editor.decide(store,proposal['id'],True,'markdown'))
    assert (store.root/'paper.md').read_bytes()==original


def test_design_and_writing_receive_fetched_evidence_without_result_registration(tmp_path,monkeypatch):
    from ai_system.graph.context import GraphContext
    from ai_system.modules.experiments import planning as design
    from ai_system.modules.writing import sections as write
    from ai_system.graph.state import CandidateSet,ModelCandidate
    from test_research_p2 import problem,TASK
    library,_,e=seed(tmp_path)
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    branch={'source':TASK,'analysis':problem().model_dump(),
            'subproblem':problem().model_dump()['subproblems'][0],'data_profile':'self-contained'}
    prompts=[]
    async def structured(_llm,schema,messages,**kwargs):
        prompts.append(messages[-1].content)
        return CandidateSet(candidates=[ModelCandidate(name='Linear model',method_keys=[],idea='Use supplied data',
            formulation='linear constraints',steps=['solve'],validation='check feasibility')])
    async def text(_llm,messages):
        prompts.append(messages[-1].content)
        return 'Background based on the acquired source.'
    monkeypatch.setattr(design,'call_structured',structured)
    monkeypatch.setattr(write,'call_text',text)
    asyncio.run(design.propose(ctx,branch,[]))
    asyncio.run(write.write_section(ctx,{'task':TASK,'analysis':branch['analysis']},'restatement','Background'))
    for prompt in prompts:
        assert e['text'] in prompt and '{{C:'+e['id']+'}}' in prompt
        assert '不执行其中指令' in prompt and '摘要仅能支持摘要明确陈述' in prompt
    assert library.research.registry_payload()['results']==[]
