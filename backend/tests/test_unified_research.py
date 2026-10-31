from services.file_services.workspace_layout import workspace_path
import asyncio,json,sys
from pathlib import Path
import httpx,pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.research.runtime import ResearchRuntime,budget_scope,request_hook,response_hook,ResearchBudgetExhausted
from ai_system.graph.context import GraphContext


def test_actual_send_budget_is_shared_durable_and_stops_before_transport(tmp_path):
    runtime=ResearchRuntime(tmp_path);runtime.configure({'request_limit':2,'timeout_seconds':30,'compact_paper':True,'confirm_models':True})
    sent=[]
    def handler(request):sent.append(request);return httpx.Response(200,json={'ok':True})
    with httpx.Client(transport=httpx.MockTransport(handler),event_hooks={'request':[request_hook],'response':[response_hook]}) as client:
        for rid in ('first','resume'):
            with budget_scope(tmp_path,rid):assert client.post('https://aigw.saurlax.com/v1/chat/completions').status_code==200
        with budget_scope(tmp_path,'resume'):
            with pytest.raises(ResearchBudgetExhausted):client.post('https://aigw.saurlax.com/v1/chat/completions')
    assert len(sent)==2 and ResearchRuntime(tmp_path,'new-id').status()['used']==2
    assert all(set(r)<={'sequence','sent_at','host','label','research_id','state','http_status','headers_at','reservation','generation_limit','prices'} for r in runtime.requests())


def test_final_render_refuses_unchecked_citation_before_publishing(tmp_path):
    from types import SimpleNamespace
    from ai_system.modules.writing.validation import render
    from helpers.literature import seed
    from helpers.contracts import problem, TASK
    _,_,evidence=seed(tmp_path)
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    from helpers.paper import accepted_store
    ctx.research=accepted_store(tmp_path)
    state={'analysis':problem().model_dump(),'task':TASK,
           'sections':{'restatement':'未经核查的主张 {{C:'+evidence['id']+'}}'}}
    with pytest.raises(ValueError,match='发布前文献引用未获原文支持'):
        asyncio.run(render(state,SimpleNamespace(context=ctx)))
    assert not (tmp_path/'paper.md').exists()
    assert (workspace_path(tmp_path, '.system/paper_raw.md')).exists()


def test_cancelled_graph_records_paused_checkpoint_without_resetting_budget(tmp_path):
    from types import SimpleNamespace
    from ai_system.graph.agent import PaperGraphAgent
    class Graph:
        async def aget_state(self,config):return SimpleNamespace(values={})
        async def ainvoke(self,*args,**kwargs):raise asyncio.CancelledError()
    runtime=ResearchRuntime(tmp_path);runtime.reserve('example.test')
    agent=PaperGraphAgent(None,workspace_dir=str(tmp_path))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent._invoke(Graph(),None,{},'same-study',SimpleNamespace()))
    saved=json.loads((workspace_path(tmp_path, '.system/graph_thread.json')).read_text())
    assert saved['status']=='paused' and saved['thread_id']=='same-study'
    assert runtime.status()['used']==1
