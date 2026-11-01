"""Paper references use the same Agent and retain reviewed, versioned edits."""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from ai_system.graph.agent import PaperGraphAgent
from ai_system.graph.context import GraphContext
from ai_system.modules.registry import invoke
from services.chat_services import paper_project as project
from services.chat_services.paper_references import PaperContext, validate_context
from services.file_services.workspace_layout import workspace_path
from research_tools_helpers import ResearchToolsModel, tool


def bound_context(store):
    document=project.snapshot(store.root)
    text=next(f['text'] for f in document['files'] if f['path']=='paper.md')
    selection={'file':'paper.md','version':document['version'],'start':0,'end':len(text),'quote':text}
    return {'research_id':store.research_id,'thread_id':None,
            'references':[{'selection':selection,'quote':text,'read_quote':None,'read_version':None}]}


def test_main_agent_proposes_reference_edits_without_overwriting_source(paper):
    store,_=paper;store.set_outcome('completed')
    context=validate_context(store.root,store.research_id,bound_context(store))
    before=(store.root/'paper.md').read_text()
    workspace_path(store.root,'.system/graph.sqlite').touch()
    def respond(messages):
        if not any(isinstance(m,ToolMessage) and m.name=='writing_propose_edits' for m in messages):
            assert any('本轮论文引用' in str(m.content) for m in messages)
            return tool('writing_propose_edits',{'edits':[{'reference':0,'replacement':'# 修改后的正文\n保留适用范围与局限。'}]},'proposal')
        return AIMessage(content='已提出修改集，请查看并接受。')
    model=ResearchToolsModel(respond=respond)
    agent=PaperGraphAgent(model,workspace_dir=str(store.root))
    asyncio.run(agent.send_message('修改引用的措辞',message_id='user:quoted',paper_context=context))
    assert (store.root/'paper.md').read_text()==before
    changes=project.read_records(store,'paper_changes')
    assert len(changes)==1 and changes[0]['status']=='pending'
    asyncio.run(project.decide(store,changes[0]['id'],'apply',compile_after=False))
    assert '修改后的正文' in (store.root/'paper.md').read_text()
    assert model.calls==2


def test_reference_turn_cannot_bypass_review_with_direct_writing(paper):
    store,_=paper;context=bound_context(store)
    ctx=GraphContext.create(str(store.root),planner_llm=None);ctx.research=store
    before=(store.root/'paper.md').read_text()
    with store._transaction() as db:
        state=store._read(db);state['conversation_input']={'id':'quoted','paper_context':context}
        store._save(db,state,'test_reference_turn',{})
    with pytest.raises(ValueError,match='writing_propose_edits'):
        asyncio.run(invoke(ctx,'writing.edit',path='paper.md',content='unreviewed overwrite'))
    assert (store.root/'paper.md').read_text()==before
    context['references'][0]['selection']=None
    with store._transaction() as db:
        state=store._read(db);state['conversation_input']['paper_context']=context
        store._save(db,state,'test_reading_quote',{})
    with pytest.raises(ValueError,match='源码范围'):
        asyncio.run(invoke(ctx,'writing.propose_edits',edits=[{'reference':0,'replacement':'guess'}]))


def test_stale_or_wrong_research_reference_is_rejected(paper):
    store,_=paper;context=bound_context(store)
    with pytest.raises(ValueError,match='研究已改变'):
        validate_context(store.root,'another-study',context)
    (store.root/'paper.md').write_text('The selected text has been replaced.')
    with pytest.raises(ValueError):
        validate_context(store.root,store.research_id,context)
    with pytest.raises(ValueError,match='引用总长度'):
        PaperContext(research_id=store.research_id,references=[{'quote':'x'*60000,'read_quote':'x'*60000}])
