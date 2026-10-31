"""Shared builders extracted from test_native_latex."""
from helpers.contracts import store_for, submit
from research_tools_helpers import answer
from ai_system.research.views import questions
from ai_system.graph.context import GraphContext
from ai_system.export.latex import native_document


def accepted_store(root):
    store = store_for(root)
    submit(store, 'Q1', {'demand': 8})
    submit(store, 'Q3', {'independent': 3})
    submit(store, 'Q2', {'allocation': 8, 'residual': 0})
    before=store.snapshot()
    assert any(q['answer_gaps'] for q in questions(before)), 'Execution results alone do not record question answers'
    results=store.registry_payload()['results']
    for question in questions(before):
        if question.get('retired'):continue
        evidence=[r for r in results if r['subproblem'] in question['experiment_ids']]
        assert evidence, 'An answered fixture question must have actual current results'
        answer(store,question_id=question['id'],
            statement='在本次给定条件下，'+'；'.join(r['name']+'为 {{R:'+r['id']+'}}' for r in evidence)+'。',
            artifact_refs=[r['id'] for r in evidence],coverage='answered',
            stop_reason='对应实验已完成冻结检查，当前问题已得到给定条件下的结果。',
            limitations=['仅限本次输入与实验条件，未验证外推适用性。'])
    after=store.snapshot()
    assert store.delivery_ready(after)
    assert after['executions']==before['executions'] and after['artifacts']==before['artifacts']
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
