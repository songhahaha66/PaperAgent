"""Shared builders extracted from test_native_latex."""
from helpers.contracts import store_for, submit
from ai_system.research.views import questions
from ai_system.graph.context import GraphContext
from helpers.native_document import native_document


def accepted_store(root):
    store = store_for(root)
    submit(store, 'Q1', {'demand': 8})
    submit(store, 'Q3', {'independent': 3})
    submit(store, 'Q2', {'allocation': 8, 'residual': 0})
    assert store.delivery_ready(store.snapshot())
    return store


def selection(store, quote, occurrence=0, file='paper.md'):
    from services.chat_services import paper_project
    doc=paper_project.snapshot(store.root)
    text=next(r['text'] for r in doc['files'] if r['path']==file)
    start=-1
    for _ in range(occurrence+1): start=text.index(quote,start+1)
    return {'file':file,'version':doc['version'],'start':start,'end':start+len(quote),'quote':quote}

def render_native_paper(store, state):
    """Build file-project fixtures through the same registry/citation renderers."""
    from ai_system.research.control import refresh_registry
    from ai_system.research.citations import render_citations
    from ai_system.graph.registry import publication_text, REF_PATTERN
    ctx=GraphContext.create(str(store.root),planner_llm=None,output_mode='latex')
    ctx.research=store
    refresh_registry(ctx)
    from ai_system.export.latex import escape_plain
    body='\n\n'.join(r'\section{'+escape_plain(key)+'}\n'+text for key,text in state['sections'].items())
    source=native_document(state['analysis']['title'],'',body)
    text=publication_text(render_citations(store.root,ctx.registry.render(source,latex=True),True))
    assert not REF_PATTERN.search(text)
    return text
