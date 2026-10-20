"""Offline contracts for file-backed comments; no paid model or research reruns."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import asyncio
import json
from types import SimpleNamespace
import pytest
from ai_system.research.models import ResearchConflict
from services.chat_services import paper_project as project, paper_comments as comments
from services.chat_services import paper_sources
from test_paper_editor import paper
from ai_system.research.literature import LiteratureStore


def run(value): return asyncio.run(value)


def selection(store, quote, occurrence=0, file='paper.md'):
    doc=project.snapshot(store.root)
    text=next(r['text'] for r in doc['files'] if r['path']==file)
    start=-1
    for _ in range(occurrence+1): start=text.index(quote,start+1)
    return {'file':file,'version':doc['version'],'start':start,'end':start+len(quote),'quote':quote}


def change(store, bound, text, thread=None):
    project.initialize(store)
    return project.propose_change(store,thread,None,[{**bound,'replacement':text}])


def test_duplicate_passage_uses_exact_file_version_range(paper):
    store,_=paper
    path=store.root/'paper.md';path.write_text('开头。重复段落。中间。重复段落。结尾。')
    bound=selection(store,'重复段落',1)
    p=change(store,bound,'正确的第二段')
    run(project.decide(store,p['id'],'apply'))
    assert path.read_text()=='开头。重复段落。中间。正确的第二段。结尾。'
    assert len(project.candidates(store.root,'重复段落'))==1


def test_unrelated_same_file_edits_survive_accept_and_revert(paper):
    store,_=paper;path=store.root/'paper.md'
    path.write_text('独立前言。\n目标段落。\n不同的结尾。')
    p=change(store,selection(store,'目标段落'),'修改后的目标')
    path.write_text('用户新增前言。\n独立前言。\n目标段落。\n不同的结尾。')
    run(project.decide(store,p['id'],'apply'))
    assert path.read_text().startswith('用户新增前言。')
    path.write_text(path.read_text()+'\n用户新增末尾。')
    run(project.decide(store,p['id'],'revert'))
    assert '目标段落' in path.read_text() and '修改后的目标' not in path.read_text()
    assert path.read_text().endswith('用户新增末尾。')


def test_changed_target_does_not_redirect_to_another_duplicate(paper):
    store,_=paper;path=store.root/'paper.md';path.write_text('重复重复')
    p=change(store,selection(store,'重复',0),'新内容')
    path.write_text('重复')
    with pytest.raises(ResearchConflict):run(project.decide(store,p['id'],'apply'))
    assert path.read_text()=='重复'


def test_target_conflict_preserves_candidate_and_user_edit(paper):
    store,_=paper;path=store.root/'paper.md';path.write_text('头部。目标。尾部。')
    p=change(store,selection(store,'目标'),'候选')
    path.write_text('头部。人工替换。尾部。')
    with pytest.raises(ResearchConflict):run(project.decide(store,p['id'],'apply'))
    assert '人工替换' in path.read_text()
    assert project.read_records(store,'paper_changes')[0]['status']=='pending'


def test_group_conflict_writes_no_files(paper):
    store,_=paper;path=store.root/'paper.tex';path.write_text('头部。目标。尾部。\\input{part}')
    (store.root/'part.tex').write_text('附录。目标。结尾。')
    a=selection(store,'目标',file='paper.tex');b=selection(store,'目标',file='part.tex')
    project.initialize(store)
    p=project.propose_change(store,None,None,[{**a,'replacement':'主文改'},{**b,'replacement':'附录改'}])
    (store.root/'part.tex').write_text('附录人工修改。')
    with pytest.raises(ResearchConflict):run(project.decide(store,p['id'],'apply'))
    assert '主文改' not in path.read_text()


def test_new_request_can_revisit_same_range_but_retry_is_deduplicated(paper):
    store,_=paper;bound=selection(store,'原来的表述',0)
    first,new=comments.create(store,'r1','解释',bound)
    same,new2=comments.create(store,'r1','解释',bound)
    second,new3=comments.create(store,'r2','再解释',bound,first['thread_id'])
    assert new and not new2 and new3 and first['id']==same['id'] and second['id']!=first['id']
    with pytest.raises(ResearchConflict):comments.create(store,'r1','不同问题',bound)


def test_thread_reanchors_to_applied_range_for_followups(paper):
    store,_=paper;bound=selection(store,'原来的表述',0)
    record,_=comments.create(store,'r1','请改',bound)
    p=change(store,bound,'修改后的表述',record['thread_id'])
    run(project.decide(store,p['id'],'apply'))
    record2,_=comments.create(store,'r2','再修改一次',None,record['thread_id'])
    assert record2['selection']['quote']=='修改后的表述'
    project.current_selection(store.root,record2['selection'])


def test_explain_does_not_create_patch(paper,monkeypatch):
    store,_=paper;bound=selection(store,'原来的表述')
    record,_=comments.create(store,'explain','这是什么意思',bound)
    async def model(*args,**kwargs):return comments.CommentPlan(intent='explain',answer='这里是在说明局限。')
    monkeypatch.setattr(comments,'call_structured',model)
    before=(store.root/'paper.md').read_text()
    result=run(comments.execute(store,record['id'],object()))
    assert result['state']=='completed' and result['answer']=='这里是在说明局限。'
    assert project.read_records(store,'paper_changes')==[] and (store.root/'paper.md').read_text()==before



def test_multiple_references_keep_ranges_and_produce_one_change_set(paper,monkeypatch):
    store,_=paper
    (store.root/'paper.md').write_text('第一段。第二段。')
    refs=[{'selection':selection(store,quote),'quote':quote,'read_quote':None,'read_version':None}
          for quote in ('第一段','第二段')]
    record,created=comments.create(store,'multi','分别改进这两段',references=refs)
    assert created and comments.create(store,'multi','分别改进这两段',references=refs)[1] is False
    seen=[]
    async def model(llm,schema,messages,**kwargs):
        context=json.loads(messages[-1].content)
        assert len(context['references'])==2
        if schema is comments.CommentPlan:return comments.CommentPlan(intent='edit')
        quote=context['selection']['quote'];seen.append(quote)
        return comments.CommentReply(answer='改进'+quote,replacement=quote+'改进')
    monkeypatch.setattr(comments,'call_structured',model)
    result=run(comments.execute(store,record['id'],object()))
    assert result['state']=='completed' and set(seen)=={'第一段','第二段'}
    patches=project.read_records(store,'paper_changes')[0]['patches']
    assert [p['quote'] for p in patches]==['第一段','第二段']
    assert project.read_records(store,'paper_threads')[0]['references']==refs
    assert (store.root/'paper.md').read_text()=='第一段。第二段。'

def test_completed_calculation_reused_when_writing_fails(paper,monkeypatch):
    store,_=paper;bound=selection(store,'原来的表述')
    record,_=comments.create(store,'compute','计算后修改',bound)
    calls={'tool':0,'model':0}
    async def model(*args,**kwargs):
        calls['model']+=1
        if calls['model']==1:return comments.CommentPlan(intent='calculate',code='print(2+2)',edit_after=True)
        if calls['model']==2:raise RuntimeError('writing unavailable')
        return comments.CommentReply(answer='实际结果为4，候选已提出。',replacement='依据实际结果修订的表述')
    async def invoke(ctx,name,**inputs):
        assert name=='experiments.comment';calls['tool']+=1
        return {'ok':True,'stdout':'4','code_path':'runs/mock/input.py','stdout_path':'runs/mock/stdout.log'}
    monkeypatch.setattr(comments,'call_structured',model)
    monkeypatch.setattr('ai_system.modules.registry.invoke',invoke)
    with pytest.raises(RuntimeError):run(comments.execute(store,record['id'],object()))
    saved=project.read_records(store,'paper_executions')[0]
    assert saved['tool_result']['stdout']=='4' and saved['phase']=='write'
    assert saved['error_type']=='RuntimeError'
    result=run(comments.execute(store,record['id'],object(),resume=True))
    assert result['state']=='completed' and result['change_id'] and calls=={'tool':1,'model':3}
    assert all(key not in result for key in ('error','error_type','owner_pid'))
    persisted=project.read_records(store,'paper_executions')[0]
    assert all(key not in persisted for key in ('error','error_type','owner_pid'))
    history=project.read_records(store,'paper_threads')[0]['messages']
    assert any(m.get('error_type')=='RuntimeError' and m['phase']=='write' for m in history)
    assert history[-1]['text']==result['answer']


def test_finish_preserves_preexisting_failure_in_thread_history(paper):
    store,_=paper
    record,_=comments.create(store,'old-failure','解释旧记录')
    record.update(state='failed',phase='write',error='先前写作未完成',error_type='RuntimeError',owner_pid=123,updated_at=1)
    with store._transaction() as db:project.put(db,store,'paper_executions',record)
    result=comments.finish(store,record,'已完成')
    assert result['state']=='completed' and all(k not in result for k in ('error','error_type','owner_pid'))
    history=project.read_records(store,'paper_threads')[0]['messages']
    assert history[-2]['text']=='先前写作未完成' and history[-2]['error_type']=='RuntimeError'
    assert history[-2]['at']==1 and history[-1]['text']=='已完成'


@pytest.mark.parametrize('repair_ok',[True,False])
def test_comment_writing_has_one_format_repair_without_repeating_tools(paper,monkeypatch,repair_ok):
    from langchain_core.messages import AIMessage
    from ai_system.graph import llm_utils
    store,_=paper;bound=selection(store,'原来的表述')
    record,_=comments.create(store,'one-repair','计算后修改',bound)
    monkeypatch.setattr(llm_utils,'RETRY_DELAY',0)
    calls={'plan':0,'write':0,'repair':0,'tool':0}
    candidate='{"replacement":"依据实际结果修订的表述"}'  # Missing required answer; never guess it.
    class Model:
        def with_structured_output(self,schema,**kwargs):
            async def structured(messages):
                if schema is comments.CommentPlan:
                    calls['plan']+=1
                    return comments.CommentPlan(intent='calculate',code='print(4)',edit_after=True)
                calls['write']+=1
                assert 'replacement字段只包含' in messages[0].content and 'answer' in messages[0].content
                return {'raw':AIMessage(content=candidate,additional_kwargs={'reasoning_content':'private reasoning'}),
                        'parsed':None,'parsing_error':None}
            return SimpleNamespace(ainvoke=structured)
        async def ainvoke(self,messages):
            calls['repair']+=1
            assert messages[-2].content==candidate and not messages[-2].additional_kwargs
            assert 'private reasoning' not in str(messages)
            return AIMessage(content=json.dumps({'answer':'实际结果为4，候选已提出。','replacement':'依据实际结果修订的表述'}) if repair_ok else candidate)
    async def invoke(ctx,name,**inputs):
        assert name=='experiments.comment';calls['tool']+=1
        return {'ok':True,'stdout':'4'}
    monkeypatch.setattr('ai_system.modules.registry.invoke',invoke)
    before=(store.root/'paper.md').read_bytes()
    if repair_ok:
        result=run(comments.execute(store,record['id'],Model()))
        assert result['state']=='completed' and result['change_id'] and result['answer']=='实际结果为4，候选已提出。'
        assert all(key not in result for key in ('error','error_type','owner_pid'))
    else:
        with pytest.raises(RuntimeError,match='CommentReply'):
            run(comments.execute(store,record['id'],Model()))
        saved=project.read_records(store,'paper_executions')[0]
        assert saved['state']=='failed' and saved['phase']=='write' and saved['tool_result']['stdout']=='4'
        assert not project.read_records(store,'paper_changes')
    assert calls=={'plan':1,'write':1,'repair':1,'tool':1}
    assert (store.root/'paper.md').read_bytes()==before


def test_latex_compile_failure_keeps_old_pdf_and_source(paper,monkeypatch):
    store,_=paper;path=store.root/'paper.tex';path.write_text('原稿中有 \\custom{宏} 和公式 $x^2$。')
    pdf=store.root/'paper.pdf';pdf.write_bytes(b'last good pdf')
    async def fail(*args):return SimpleNamespace(pdf_path=None,error='花括号未配对',warnings=[])
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',fail)
    p=change(store,selection(store,'原稿',file='paper.tex'),'新稿')
    run(project.decide(store,p['id'],'apply'))
    assert pdf.read_bytes()==b'last good pdf' and '\\custom{宏}' in path.read_text()
    doc=project.describe(store)
    assert doc['pdf_available'] and doc['compile']['state']=='failed' and '花括号' in doc['compile']['message']


def test_latex_success_updates_pdf_at_exact_project_version(paper,monkeypatch):
    store,_=paper;(store.root/'paper.tex').write_text('源文件。')
    async def success(folder,*args):
        (folder/'paper.pdf').write_bytes(b'new pdf')
        return SimpleNamespace(pdf_path='paper.pdf',warnings=[])
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',success)
    run(project.compile_project(store))
    doc=project.describe(store)
    assert doc['pdf_version']==doc['version'] and doc['compile']['state']=='current'
    (store.root/'paper.tex').write_text('人工更新。')
    assert project.describe(store)['compile']['state']=='stale'
    assert (store.root/'paper.pdf').read_bytes()==b'new pdf'


def test_compile_does_not_publish_stale_result(paper,monkeypatch):
    store,_=paper;path=store.root/'paper.tex';path.write_text('原稿。')
    pdf=store.root/'paper.pdf';pdf.write_bytes(b'old')
    async def success(folder,*args):
        path.write_text('编译期间修改。');(folder/'paper.pdf').write_bytes(b'stale')
        return SimpleNamespace(pdf_path='paper.pdf',warnings=[])
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',success)
    run(project.compile_project(store))
    assert pdf.read_bytes()==b'old' and project.meta(store.root)['compile']['state']=='stale'


def test_source_card_quote_version_page_and_bib_are_real(paper):
    store,_=paper;literature=LiteratureStore(store.root)
    source=literature.upsert({'title':'实际论文','url':'https://example.org/paper','authors':['Author'],'year':2025})
    text='This is the actual original passage from the retrieved article and cannot be invented.'
    literature.add_passages(source,[{'text':text,'locator':'摘要段落 1','page':None}],'abstract',source['url'],'f'*64)
    card=paper_sources.cards(store)[0]
    assert card['excerpt']==text and card['abstract_only'] and card['version']=='f'*64 and card['page'] is None
    paper_sources.write_bibliography(store.root)
    bib=(store.root/'paper-sources.bib').read_text()
    assert '@misc{'+card['key'] in bib and '实际论文' in bib
    with pytest.raises(ValueError):paper_sources.validate_references(store.root,'',r'新增引用 \cite{invented}')


def test_legacy_import_keeps_tex_bib_and_custom_files(paper):
    store,_=paper;tex='\\documentclass{custom}\n\\newcommand{\\macro}[1]{#1}\n\\input{part}\n\\bibliography{refs}\n'
    (store.root/'paper.tex').write_text(tex);(store.root/'part.tex').write_text('公式 $x^2$ 与表格。')
    (store.root/'refs.bib').write_text('@misc{old,title={Existing}}');(store.root/'custom.cls').write_text('custom class')
    old=(store.root/'paper.md').read_bytes();doc=project.describe(store)
    assert {r['path'] for r in doc['files']}=={'paper.tex','part.tex','refs.bib','custom.cls'}
    assert (store.root/'paper.tex').read_text()==tex and (store.root/'paper.md').read_bytes()==old
    assert project.base_text(store.root,doc['version'],'paper.tex')==tex


def test_malformed_offset_and_path_are_rejected(paper):
    store,_=paper;bound=selection(store,'原来的表述')
    with pytest.raises(ResearchConflict):project.bind(store.root,{**bound,'start':bound['start']+1})
    with pytest.raises(ValueError):project.safe_path(store.root,'../secret.tex')
    with pytest.raises(ValueError):project.safe_path(store.root,'.system/private.tex')



def test_native_dependency_snapshot_includes_registered_detokenize_figure(paper):
    store,_=paper;(store.root/'outputs').mkdir(exist_ok=True)
    (store.root/'outputs/figure_1.png').write_bytes(b'image bytes')
    (store.root/'paper.tex').write_text(r'\includegraphics[width=.8\linewidth]{\detokenize{outputs/figure_1.png}}')
    assert 'outputs/figure_1.png' in {row['path'] for row in project.snapshot(store.root)['files']}


def test_group_write_failure_restores_all_sources(paper,monkeypatch):
    store,_=paper;(store.root/'paper.tex').write_text('主文目标。\\input{part}')
    (store.root/'part.tex').write_text('附录目标。')
    a=selection(store,'目标',file='paper.tex');b=selection(store,'目标',file='part.tex')
    project.initialize(store)
    p=project.propose_change(store,None,None,[{**a,'replacement':'主改'},{**b,'replacement':'附改'}])
    original=project.put
    def fail(db,store,table,value):
        if table=='paper_changes' and value['status']=='applied':raise RuntimeError('injected')
        return original(db,store,table,value)
    monkeypatch.setattr(project,'put',fail)
    with pytest.raises(RuntimeError,match='injected'):run(project.decide(store,p['id'],'apply'))
    assert (store.root/'paper.tex').read_text()=='主文目标。\\input{part}'
    assert (store.root/'part.tex').read_text()=='附录目标。'
    assert project.read_records(store,'paper_changes')[0]['status']=='pending'


def test_recovery_preserves_edit_made_after_interrupted_save(paper):
    store,_=paper;project.initialize(store)
    path=store.root/'paper.md';path.write_text('后来的人工作文。')
    project.atomic_text(store.root/project.MARKER,json.dumps({'id':'missing','decision':'applied','before':{'paper.md':'旧稿。'},'after':{'paper.md':'中断稿。'}}))
    project.recover(store)
    assert path.read_text()=='后来的人工作文。' and '人工稿' in project.meta(store.root)['migration_notice']


def test_initial_empty_file_can_be_edited_and_reverted(paper):
    store,_=paper;path=store.root/'paper.md';path.write_text('')
    doc=project.snapshot(store.root);bound={'file':'paper.md','version':doc['version'],'start':0,'end':0,'quote':''}
    p=change(store,bound,'第一版原稿。')
    run(project.decide(store,p['id'],'apply'))
    assert path.read_text()=='第一版原稿。'
    run(project.decide(store,p['id'],'revert'))
    assert path.read_text()==''


def test_real_auxiliary_calculation_freezes_inputs_and_keeps_run_provenance(paper):
    from ai_system.graph.context import GraphContext
    from ai_system.modules.registry import invoke
    store,_=paper;(store.root/'data').mkdir(exist_ok=True);(store.root/'data/value.txt').write_text('4')
    ctx=GraphContext.create(str(store.root),planner_llm=None);ctx.research=store
    before=store.snapshot()
    result=run(invoke(ctx,'experiments.comment',request_id='offline-calc',code="from pathlib import Path\nprint(int(Path('data/value.txt').read_text()) * 2)",inputs=['data/value.txt']))
    assert result['ok'] and result['stdout'].strip()=='8'
    assert (store.root/result['code_path']).is_file() and (store.root/result['stdout_path']).read_text().strip()=='8'
    (store.root/'data/value.txt').write_text('100')
    frozen=store.root/result['folder']/'data/value.txt'
    assert frozen.read_text()=='4' and result['inputs'][0]['hash']
    assert store.snapshot()['executions']==before['executions']


def test_comment_and_source_routes_keep_owner_guard_and_accept_drafts(paper,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.work_routes import workspace
    from auth.auth import get_current_user
    from database.database import get_db
    store,_=paper
    app=FastAPI();app.include_router(workspace.router)
    app.dependency_overrides[get_current_user]=lambda:7
    app.dependency_overrides[get_db]=lambda:object()
    monkeypatch.setattr(workspace.crud,'get_work',lambda *args:SimpleNamespace(created_by=7,output_mode='markdown'))
    monkeypatch.setattr(workspace.workspace_file_service,'get_workspace_path',lambda *args:store.root)
    monkeypatch.setattr(workspace,'_paper_writing_model',lambda *args:object())
    async def model(*args,**kwargs):return comments.CommentPlan(intent='explain',answer='已解释选区。')
    monkeypatch.setattr(comments,'call_structured',model)
    with TestClient(app) as client:
        doc=client.get('/api/workspace/work/paper/project').json()
        bound=selection(store,'原来的表述')
        body={'research_id':store.research_id,'request_id':'api-comment','message':'请解释','selection':bound}
        assert client.post('/api/workspace/work/paper/comments',json=body).status_code==202
        assert client.post('/api/workspace/work/paper/comments',json=body).status_code==202
        doc=client.get('/api/workspace/work/paper/project').json()
        assert len(doc['executions'])==1 and len(doc['threads'])==1
        assert doc['executions'][0]['state']=='completed'
        assert client.post('/api/workspace/work/paper/locate',json={'quote':'原来的表述'}).json()['confirmation_required']
        monkeypatch.setattr(workspace.crud,'get_work',lambda *args:SimpleNamespace(created_by=8))
        assert client.get('/api/workspace/work/paper/project').status_code==403
        assert client.post('/api/workspace/work/paper/comments',json=body).status_code==403


def test_bibliography_refresh_preserves_user_edits(paper):
    store,_=paper;literature=LiteratureStore(store.root)
    source=literature.upsert({'title':'Original title','url':'https://example.org/one'})
    literature.add_passages(source,[{'text':'Actual retrieved original passage from a paper, preserved for the source card.'}],'abstract',source['url'],'a'*64)
    paper_sources.write_bibliography(store.root)
    path=store.root/'paper-sources.bib';path.write_text(path.read_text().replace('Original title','User corrected title'))
    paper_sources.write_bibliography(store.root)
    assert 'User corrected title' in path.read_text()
    assert '人工编辑' in project.meta(store.root)['migration_notice']


def test_local_patch_preserves_crlf_and_non_ascii_outside_selection(paper):
    store,_=paper;path=store.root/'paper.md';path.write_bytes('前言😀。\r\n目标。\r\n末尾。\r\n'.encode())
    doc=project.snapshot(store.root);text=doc['files'][0]['text'];start=text.index('目标')
    bound={'file':'paper.md','version':doc['version'],'start':start,'end':start+2,'quote':'目标'}
    p=change(store,bound,'修订')
    run(project.decide(store,p['id'],'apply'))
    assert path.read_bytes()=='前言😀。\r\n修订。\r\n末尾。\r\n'.encode()
