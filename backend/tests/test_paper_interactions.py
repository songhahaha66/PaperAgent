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


def test_comment_explains_without_patch_and_multiple_references_share_one_change_set(paper):
    from research_tools_helpers import ResearchToolsModel,tool
    store,_=paper
    record,_=comments.create(store,'explain','这是什么意思',selection(store,'原来的表述'))
    before=(store.root/'paper.md').read_text()
    model=ResearchToolsModel(respond=lambda messages:tool('CommentReply',{'answer':'这里是在说明局限。'}))
    result=run(comments.execute(store,record['id'],model))
    assert result['answer']=='这里是在说明局限。' and not project.read_records(store,'paper_changes')
    assert (store.root/'paper.md').read_text()==before
    (store.root/'paper.md').write_text('第一段。第二段。')
    refs=[{'selection':selection(store,q),'quote':q} for q in ('第一段','第二段')]
    record,_=comments.create(store,'multi','分别改进这两段',references=refs)
    def respond(messages):
        if not any(getattr(m,'name',None)=='propose_edits' for m in messages):
            return tool('propose_edits',{'edits':[{'reference':i,'replacement':q+'改进'} for i,q in enumerate(('第一段','第二段'))]},'edit')
        return tool('CommentReply',{'answer':'两处修改已提出，等待接受。'})
    result=run(comments.execute(store,record['id'],ResearchToolsModel(respond=respond)))
    patches=project.read_records(store,'paper_changes')[0]['patches']
    assert [p['quote'] for p in patches]==['第一段','第二段']
    assert (store.root/'paper.md').read_text()=='第一段。第二段。'
    assert result['steps'][0]['state']=='completed'


def test_comment_reads_excel_corrects_errors_and_resumes_without_replaying_tools(paper,monkeypatch):
    from research_tools_helpers import ResearchToolsModel,tool
    from langchain_core.messages import ToolMessage
    from openpyxl import Workbook
    store,_=paper;before=store.snapshot()
    folder=store.root/'attachment';folder.mkdir(exist_ok=True)
    book=Workbook();sheet=book.active;sheet.title='名单'
    sheet.append(['姓名','队伍']);sheet.append(['同名学生','A队']);sheet.append(['同名学生','B队'])
    book.save(folder/'名单.xlsx')
    record,_=comments.create(store,'lookup','他们在两支队伍吗？在哪？')
    calls=[];failed=[False]
    async def unavailable(self,query,**kwargs):
        return {'sources':[],'search':{'status':'failed','errors':[{'message':'检索服务不可用'}]}}
    monkeypatch.setattr('ai_system.research.literature.LiteratureService.search',unavailable)
    def respond(messages):
        observations=[m for m in messages if isinstance(m,ToolMessage)]
        if len(observations)==0:return tool('search_literature',{'query':'队伍名单'},'search')
        if len(observations)==1:
            assert observations[-1].status=='error' and '检索服务不可用' in observations[-1].content
            return tool('run_python',{'code':"import pandas as pd; df=pd.read_excel('attachment/错误.xlsx')"},'bad-path')
        if len(observations)==2:
            assert observations[-1].status=='error'
            return tool('run_python',{'code':"import pandas as pd; df=pd.read_excel('attachment/名单.xlsx', sheet_name='名单'); print(df.to_string(index=False))"},'read')
        if len(observations)==3:
            return tool('run_python',{'code':"print('Excel行号:', (df.index+2).tolist()); print('不同队伍数:', df['队伍'].nunique())"},'check')
        assert '不同队伍数: 2' in observations[-1].content
        if not failed[0]:failed[0]=True;raise RuntimeError('reply unavailable')
        return tool('CommentReply',{'answer':'附件名单.xlsx 的名单工作表，第2、3行分属A队、B队；姓名相同，尚不能确认同一人。'})
    model=ResearchToolsModel(respond=respond)
    with pytest.raises(RuntimeError,match='reply unavailable'):run(comments.execute(store,record['id'],model))
    saved=project.read_records(store,'paper_executions')[0]
    assert [s['state'] for s in saved['steps']]==['failed','failed','completed','completed']
    assert 'reply unavailable' in saved['error']
    assert len(list((store.root/'.system/paper-comment-runs/lookup/runs').iterdir()))==3
    result=run(comments.execute(store,record['id'],model,resume=True))
    assert result['state']=='completed' and '第2、3行' in result['answer']
    assert len(result['steps'])==4 and len(list((store.root/'.system/paper-comment-runs/lookup/runs').iterdir()))==3
    assert not project.read_records(store,'paper_changes')
    assert store.snapshot()['executions']==before['executions'] and store.snapshot()['artifacts']==before['artifacts']
    for step in result['steps'][2:]:
        assert (store.root/step['result']['code_path']).is_file() and (store.root/step['result']['stdout_path']).is_file()
    assert any(c['key'].startswith('Elookup_') for c in paper_sources.cards(store))


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



def test_latex_compile_failure_keeps_old_pdf_and_source(paper,monkeypatch):
    store,_=paper;path=store.root/'paper.tex';path.write_text('原稿中有 \\custom{宏} 和公式 $x^2$。')
    pdf=store.root/'paper.pdf';pdf.write_bytes(b'last good pdf')
    from ai_system.export.latex import LatexResult
    async def fail(*args):return LatexResult('paper.tex',error='花括号未配对')
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',fail)
    p=change(store,selection(store,'原稿',file='paper.tex'),'新稿')
    run(project.decide(store,p['id'],'apply'))
    assert pdf.read_bytes()==b'last good pdf' and '\\custom{宏}' in path.read_text()
    doc=project.describe(store)
    assert doc['pdf_available'] and doc['compile']['state']=='failed' and '花括号' in doc['compile']['message']


def test_latex_success_updates_pdf_at_exact_project_version(paper,monkeypatch):
    store,_=paper;(store.root/'paper.tex').write_text('源文件。')
    async def success(folder,*args):
        from ai_system.export.latex import LatexResult
        (folder/'paper.pdf').write_bytes(b'new pdf')
        return LatexResult('paper.tex',pdf_path='paper.pdf')
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
        from ai_system.export.latex import LatexResult
        path.write_text('编译期间修改。');(folder/'paper.pdf').write_bytes(b'stale')
        return LatexResult('paper.tex',pdf_path='paper.pdf')
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



def test_comment_and_source_routes_keep_owner_guard_and_accept_drafts(paper,monkeypatch):
    store,_=paper
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(store.root/'api-test.sqlite'))
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
    from research_tools_helpers import ResearchToolsModel,tool
    model=ResearchToolsModel(respond=lambda messages:tool('CommentReply',{'answer':'已解释选区。'}))
    monkeypatch.setattr(workspace,'_paper_writing_model',lambda *args:model)
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
