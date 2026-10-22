import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import json
import shutil
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ai_system.research.models import ResearchConflict
from services.chat_services import paper_editor as editor
from helpers.paper import accepted_store
from helpers.contracts import problem, TASK
from ai_system.modules.writing.validation import assemble
from ai_system.modules.writing.sections import section_plan
from ai_system.research.control import graph_view,paper_state


@pytest.fixture
def paper(tmp_path):
    store = accepted_store(tmp_path)
    state = graph_view(store.snapshot())
    sections = {key: '原来的表述说明研究范围与局限。' * 8 for key, _, _ in section_plan(state) if key != 'solve_heading'}
    state['sections'] = sections
    store.save_sections(sections, [])
    store.publish_paper(assemble(state))
    (tmp_path / '.system/graph_thread.json').write_text(json.dumps({'thread_id': store.research_id, 'engine_version': 'research-v3'}))
    return store, state


def candidate(store):
    before = store.snapshot()['sections']['solve:Q1']['text']
    return asyncio.run(editor.propose(store, 'solve:Q1', '简洁表述', before.replace('原来的表述', '修订后的表述')))


@pytest.mark.skipif(not shutil.which('pandoc'),reason='pandoc unavailable')
def test_saved_word_document_opens_and_edits_without_a_graph_checkpoint(paper):
    store,_=paper
    checkpoint=store.root/'.system/graph.sqlite'
    assert not checkpoint.exists()
    before=store.snapshot()
    assert asyncio.run(editor.document(store))['sections']
    proposal=candidate(store)
    assert asyncio.run(editor.decide(store,proposal['id'],True,'word'))['status']=='applied'
    assert (store.root/'paper.docx').read_bytes().startswith(b'PK')
    assert '修订后的表述' in (store.root/'paper.md').read_text()
    assert store.snapshot()['executions']==before['executions'] and not checkpoint.exists()


def test_saved_store_requirements_outline_and_judgments_override_an_old_checkpoint(paper):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from ai_system.graph.build import compile_graph
    from services.chat_services import review_tasks
    store,_=paper
    async def save_old_checkpoint():
        async with AsyncSqliteSaver.from_conn_string(str(store.root/'.system/graph.sqlite')) as saver:
            await compile_graph(saver).aupdate_state({'configurable':{'thread_id':store.research_id}},
                {'task':'OUTDATED_REQUEST','analysis':{'title':'OUTDATED_TITLE'},
                 'outline':[{'key':'old','title':'OUTDATED_OUTLINE'}]},as_node='solve_join')
    asyncio.run(save_old_checkpoint())
    outline=[{'key':'evaluation','title':'当前结论'},{'key':'solve_heading','title':'当前研究'}]
    with store._transaction() as db:
        saved=store._read(db)
        saved['spec']['requirement']+='\n当前补充要求。'
        saved['spec']['requirements'][0]['statement']='当前要求记录'
        saved['publication']={'title':'当前论文标题','outline':outline}
        saved['consistency']=[{'resolution':'当前已保存说明'}]
        next(iter(saved['questions'].values()))['judgment']='当前问题判断'
        store._save(db,saved,'fixture_current_material',{})
    snapshot=store.snapshot();state=paper_state(store)
    assert state==editor.paper_state(store)==review_tasks.paper_state(store)
    assert state['task']==snapshot['spec']['requirement'] and state['analysis']['title']=='当前论文标题'
    assert state['outline']==outline and state['consistency']==snapshot['consistency']
    assert state['analysis']['requirements']==snapshot['spec']['requirements']
    assert state['analysis']['questions']==graph_view(snapshot)['analysis']['questions']
    assert 'OUTDATED_' not in json.dumps(state)
    doc=asyncio.run(editor.document(store))
    assert doc['title']=='当前论文标题' and doc['outline'][0]['title']=='当前结论'


def test_historical_sections_remain_visible_without_becoming_publishable(paper):
    store,_=paper
    with store._transaction() as db:
        saved=store._read(db);saved['sections']['abstract']['validity']='stale'
        store._save(db,saved,'fixture_stale_section',{})
    snapshot=store.snapshot()
    assert 'abstract' not in graph_view(snapshot)['sections']
    assert paper_state(store)['sections']['abstract']==snapshot['sections']['abstract']['text']
    doc=asyncio.run(editor.document(store))
    assert next(s for s in doc['sections'] if s['key']=='abstract')['validity']=='stale'
    assert not doc['artifacts']
    with pytest.raises(ResearchConflict):asyncio.run(editor.propose(store,'abstract','修改旧稿',content='新的文字'))


def test_shared_native_view_uses_real_files_and_versions_instead_of_saved_sections(paper):
    from ai_system.export.latex import native_document
    from services.chat_services import paper_project as project,review_tasks
    store,_=paper
    with store._transaction() as db:
        saved=store._read(db);saved['spec']['output_mode']='latex'
        store._save(db,saved,'fixture_native_output',{})
    source=native_document('真实源码','摘要',r'\section{当前正文}实际文件内容。')
    (store.root/'paper.tex').write_text(source)
    project.initialize(store)
    before=store.snapshot();state=paper_state(store)
    assert state==review_tasks.paper_state(store)
    assert state['sections']=={'paper.tex':source}
    assert state['source_version']==project.snapshot(store.root)['version']
    assert all(not k.startswith('solve:') for k in state['sections'])
    assert store.snapshot()==before


def test_document_order_matches_assembled_outline(paper):
    store, state = paper
    doc = asyncio.run(editor.document(store))
    expected = [key for key, _, _ in section_plan(state) if key in store.snapshot()['sections']]
    assert [section['key'] for section in doc['sections']] == expected
    assert [(entry['key'], entry['title'], entry['level']) for entry in doc['outline']] == section_plan(state)
    group = next(entry for entry in doc['outline'] if entry['key'] == 'solve_heading')
    assert not group['available']  # A structural heading need not have its own text.


def test_document_never_promotes_an_old_pdf_by_file_existence(paper):
    store, _ = paper
    (store.root / 'paper.pdf').write_bytes(b'old-export')
    assert 'paper.pdf' not in asyncio.run(editor.document(store))['artifacts']
    store.register_exports(['paper.pdf'])
    assert 'paper.pdf' in asyncio.run(editor.document(store))['artifacts']
    (store.root / 'paper.md').write_text('人工修改')
    document=asyncio.run(editor.document(store))
    assert not document['artifacts'] and document['publication_notice']


def test_review_apply_is_section_local_versioned_and_idempotent(paper):
    store, _ = paper
    before = store.snapshot()
    original = (store.root / 'paper.md').read_text()
    p = candidate(store)
    assert store.snapshot() == before and (store.root / 'paper.md').read_text() == original
    result = asyncio.run(editor.decide(store, p['id'], True, 'markdown'))
    assert result['status'] == 'applied'
    after = store.snapshot()
    assert after['executions'] == before['executions'] and after['experiments'] == before['experiments']
    for key in before['sections']:
        if key != 'solve:Q1':
            assert after['sections'][key] == before['sections'][key]
    assert after['sections']['solve:Q1']['revision'] == before['sections']['solve:Q1']['revision'] + 1
    assert '修订后的表述' in (store.root / 'paper.md').read_text()
    assert list((store.root / '.system/research' / store.research_id / 'papers').glob('*before-edit.md'))
    asyncio.run(editor.decide(store, p['id'], True, 'markdown'))
    assert store.snapshot() == after


def test_reject_and_stale_or_manual_edits_do_not_overwrite(paper):
    store, _ = paper
    p = candidate(store)
    before = store.snapshot()
    asyncio.run(editor.decide(store, p['id'], False, 'markdown'))
    assert store.snapshot() == before
    p = candidate(store)
    (store.root / 'paper.md').write_text('人工修改不能丢失')
    with pytest.raises(ResearchConflict):
        asyncio.run(editor.decide(store, p['id'], True, 'markdown'))
    assert (store.root / 'paper.md').read_text() == '人工修改不能丢失'
    assert editor.get_proposal(store, p['id'])['status'] == 'pending'


def test_parallel_candidates_conflict(paper):
    store, _ = paper
    first, second = candidate(store), candidate(store)
    asyncio.run(editor.decide(store, first['id'], True, 'markdown'))
    with pytest.raises(ResearchConflict):
        asyncio.run(editor.decide(store, second['id'], True, 'markdown'))


@pytest.mark.parametrize('after', ['结果 9 {{R:R1}}', '结果 8 {{R:R2}}', '结果 8 {{R:R1}} $x=z$'])
def test_wording_cannot_change_numeric_evidence_or_math(after):
    with pytest.raises(ValueError):
        editor.guard_wording('结果 8 {{R:R1}}', after, False)


def test_native_legacy_proposals_cannot_overwrite_file_project(paper):
    store, state = paper
    from helpers.paper import render_native_paper
    store.publish_paper(render_native_paper(store,state), filename='paper.tex')
    original = (store.root / 'paper.tex').read_text()
    with pytest.raises(ResearchConflict, match='源码选区评论'):
        candidate(store)
    assert (store.root / 'paper.tex').read_text() == original


def test_api_review_flow_and_ownership(paper, monkeypatch):
    from routers.work_routes import workspace
    from auth.auth import get_current_user
    from database.database import get_db
    store, _ = paper
    app = FastAPI(); app.include_router(workspace.router)
    app.dependency_overrides[get_current_user] = lambda: 7
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(workspace.crud, 'get_work', lambda *args: SimpleNamespace(created_by=7, output_mode='markdown'))
    monkeypatch.setattr(workspace.workspace_file_service, 'get_workspace_path', lambda *args: store.root)
    with TestClient(app) as client:
        document = client.get('/api/workspace/work/paper').json()
        section = next(s for s in document['sections'] if s['key'] == 'solve:Q1')
        request = dict(research_id=store.research_id, base_commit=document['commit_seq'], section='solve:Q1', content=section['text'].replace('原来', '修改'))
        response = client.post('/api/workspace/work/paper/proposals', json=request)
        assert response.status_code == 200, response.text
        p = response.json()
        response = client.post('/api/workspace/work/paper/proposals/' + p['id'], json={'research_id': store.research_id, 'decision': 'apply'})
        assert response.status_code == 200 and response.json()['status'] == 'applied'
        assert client.post('/api/workspace/work/paper/proposals', json=request).status_code == 409
        monkeypatch.setattr(workspace.crud, 'get_work', lambda *args: SimpleNamespace(created_by=8))
        assert client.get('/api/workspace/work/paper').status_code == 403
        assert client.post('/api/workspace/work/paper/proposals', json=request).status_code == 403


def test_ai_proposal_route_uses_saved_step_model_with_bounded_defaults(paper, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from models.models import Base, ModelConfig, Project
    from routers.work_routes import workspace
    from auth.auth import get_current_user
    from database.database import get_db
    store, _ = paper
    engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(Project(work_id='step-route', title='Paper', created_by=7, output_mode='markdown'))
        db.add(ModelConfig(type='writing', model_id='step-5-preview', api_key='fixture',
                           base_url='https://aigw.saurlax.com/v1', created_by=7))
        db.commit()
    def database():
        with sessions() as db:
            yield db
    captured = []
    async def propose(store, section, instruction, content, llm):
        captured.append(llm)
        return {'id':'parameters-checked'}
    monkeypatch.setattr(editor, 'propose', propose)
    monkeypatch.setattr(workspace.workspace_file_service, 'get_workspace_path', lambda _: store.root)
    app = FastAPI(); app.include_router(workspace.router)
    app.dependency_overrides[get_current_user] = lambda: 7
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        response = client.post('/api/workspace/step-route/paper/proposals', json={
            'research_id':store.research_id, 'base_commit':store.snapshot()['commit_seq'],
            'section':'solve:Q1', 'instruction':'简洁表述'})
        assert response.status_code == 200, response.text
    llm = captured[0]
    assert llm.extra_body == {'reasoning_effort':'low'}
    assert llm.request_timeout == 600 and llm.max_retries == 0
    engine.dispose()


def test_data_change_invalidates_pending_wording_candidate(paper):
    store, _ = paper
    proposal = candidate(store)
    source = (store.root / 'paper.md').read_text()
    data = store.root / 'data'
    data.mkdir(exist_ok=True)
    (data / 'corrected.csv').write_text('x,y\n1,2\n')
    with pytest.raises(ResearchConflict):
        asyncio.run(editor.decide(store, proposal['id'], True, 'markdown'))
    assert (store.root / 'paper.md').read_text() == source
    assert any(t['validity'] == 'stale' for t in store.snapshot()['experiments'].values())


@pytest.mark.parametrize('failure', ['source_replace', 'save'])
def test_partial_publication_failure_restores_files_and_database_in_other_modes(paper, monkeypatch, failure):
    store, state = paper
    before = store.snapshot()
    source = (store.root / 'paper.md').read_bytes()
    proposal = candidate(store)
    if failure == 'save':
        save = store._save
        def fail_save(db, snapshot, kind, payload):
            if kind == 'section_edit_applied':
                raise RuntimeError('injected journal failure')
            return save(db, snapshot, kind, payload)
        monkeypatch.setattr(store, '_save', fail_save)
    else:
        replace = Path.replace
        def fail_replace(path, target):
            if path.name == 'paper.md.edit-tmp':
                raise RuntimeError('injected source replacement failure')
            return replace(path, target)
        monkeypatch.setattr(Path, 'replace', fail_replace)
    with pytest.raises(RuntimeError, match='injected'):
        asyncio.run(editor.decide(store, proposal['id'], True, 'markdown'))
    assert store.snapshot() == before
    assert (store.root / 'paper.md').read_bytes() == source
    assert editor.get_proposal(store, proposal['id'])['status'] == 'pending'
    assert not (store.root / editor.PUBLICATION_MARKER).exists()


def test_interrupted_publication_recovers_on_next_paper_request(paper, tmp_path):
    store, _ = paper
    before = (store.root / 'paper.md').read_bytes()
    proposal = candidate(store)
    staging = tmp_path / 'staging'
    staging.mkdir()
    (staging / 'paper.md').write_text('uncommitted source')
    (staging / 'paper.pdf').write_bytes(b'uncommitted PDF')
    with store._transaction():
        editor.prepare_publication(store, proposal['id'], staging, ['paper.md', 'paper.pdf'])
    assert (store.root / 'paper.md').read_bytes() != before
    editor.current_store(store.root)
    assert (store.root / 'paper.md').read_bytes() == before
    assert not (store.root / 'paper.pdf').exists()
    assert editor.get_proposal(store, proposal['id'])['status'] == 'pending'
