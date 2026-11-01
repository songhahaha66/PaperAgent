"""Offline contracts for file-backed comments; no paid model or research reruns."""
from services.file_services.workspace_layout import workspace_path
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import asyncio
import json
from types import SimpleNamespace
import pytest
from ai_system.research.models import ResearchConflict
from services.chat_services import paper_project as project
from services.chat_services import paper_sources
from helpers.paper import selection


pytestmark = pytest.mark.usefixtures("offline_kernel")

def run(value): return asyncio.run(value)


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


def test_latex_compile_failure_keeps_old_pdf_and_source(paper,monkeypatch):
    store,_=paper;path=store.root/'paper.tex';path.write_text('原稿中有 \\custom{宏} 和公式 $x^2$。')
    pdf=store.root/'paper.pdf';pdf.write_bytes(b'last good pdf')
    from ai_system.export.latex import LatexResult
    async def fail(*args,**kwargs):return LatexResult('paper.tex',error='花括号未配对')
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',fail)
    p=change(store,selection(store,'原稿',file='paper.tex'),'新稿')
    run(project.decide(store,p['id'],'apply'))
    assert pdf.read_bytes()==b'last good pdf' and '\\custom{宏}' in path.read_text()
    doc=project.describe(store)
    assert doc['pdf_available'] and doc['compile']['state']=='failed' and '花括号' in doc['compile']['message']


def test_compile_does_not_publish_stale_result(paper,monkeypatch):
    store,_=paper;path=store.root/'paper.tex';path.write_text('原稿。')
    pdf=store.root/'paper.pdf';pdf.write_bytes(b'old')
    async def success(folder,*args,**kwargs):
        from ai_system.export.latex import LatexResult
        path.write_text('编译期间修改。');(folder/'paper.pdf').write_bytes(b'stale')
        return LatexResult('paper.tex',pdf_path='paper.pdf')
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',success)
    run(project.compile_project(store))
    assert pdf.read_bytes()==b'old' and project.meta(store.root)['compile']['state']=='stale'


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
    project.atomic_text(workspace_path(store.root, project.MARKER),json.dumps({'id':'missing','decision':'applied','before':{'paper.md':'旧稿。'},'after':{'paper.md':'中断稿。'}}))
    project.recover(store)
    assert path.read_text()=='后来的人工作文。' and '人工稿' in project.meta(store.root)['migration_notice']


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
    app.dependency_overrides[get_db]=lambda:SimpleNamespace(close=lambda:None)
    monkeypatch.setattr(workspace.crud,'get_work',lambda *args:SimpleNamespace(architecture_version=2, created_by=7,output_mode='markdown'))
    monkeypatch.setattr(workspace.workspace_file_service,'get_workspace_path',lambda *args:store.root)
    with TestClient(app) as client:
        doc=client.get('/api/workspace/work/paper/project').json()
        bound=selection(store,'原来的表述')
        body={'research_id':store.research_id,'request_id':'api-comment','message':'请解释','selection':bound}
        assert client.post('/api/workspace/work/paper/comments',json=body).status_code==404
        assert client.post('/api/workspace/work/paper/comments',json=body).status_code==404
        doc=client.get('/api/workspace/work/paper/project').json()
        assert doc['executions']==[] and doc['threads']==[]
        assert client.post('/api/workspace/work/paper/locate',json={'quote':'原来的表述'}).json()['confirmation_required']
        monkeypatch.setattr(workspace.crud,'get_work',lambda *args:SimpleNamespace(architecture_version=2, created_by=8))
        assert client.get('/api/workspace/work/paper/project').status_code==403
        assert client.post('/api/workspace/work/paper/locate',json={'quote':'原来的表述'}).status_code==403
