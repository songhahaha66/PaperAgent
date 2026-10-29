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
from services.file_services.workspace_layout import workspace_path
from ai_system.modules.writing.validation import assemble
from ai_system.modules.writing.sections import section_plan
from ai_system.research.control import graph_view


@pytest.fixture
def paper(tmp_path):
    store = accepted_store(tmp_path)
    state = graph_view(store.snapshot())
    sections = {key: '原来的表述说明研究范围与局限。' * 8 for key, _, _ in section_plan(state) if key != 'solve_heading'}
    state['sections'] = sections
    store.save_sections(sections, [])
    store.publish_paper(assemble(state))
    workspace_path(tmp_path,'.system/graph_thread.json').write_text(json.dumps({'thread_id': store.research_id, 'engine_version': 'research-v3'}))
    return store, state


def candidate(store):
    before = store.snapshot()['sections']['solve:Q1']['text']
    return asyncio.run(editor.propose(store, 'solve:Q1', '简洁表述', before.replace('原来的表述', '修订后的表述')))


@pytest.mark.skipif(not shutil.which('pandoc'),reason='pandoc unavailable')
def test_saved_word_document_opens_and_edits_without_a_graph_checkpoint(paper, offline_compiler):
    store,_=paper
    checkpoint=workspace_path(store.root, '.system/graph.sqlite')
    assert not checkpoint.exists()
    before=store.snapshot()
    assert asyncio.run(editor.document(store))['sections']
    proposal=candidate(store)
    assert asyncio.run(editor.decide(store,proposal['id'],True,'word'))['status']=='applied'
    assert (store.root/'paper.docx').read_bytes().startswith(b'PK')
    assert '修订后的表述' in (store.root/'paper.md').read_text()
    assert store.snapshot()['executions']==before['executions'] and not checkpoint.exists()


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
    assert list((workspace_path(store.root, '.system/research') / store.research_id / 'papers').glob('*before-edit.md'))
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


def test_api_review_flow_and_ownership(paper, monkeypatch):
    from routers.work_routes import workspace
    from auth.auth import get_current_user
    from database.database import get_db
    store, _ = paper
    app = FastAPI(); app.include_router(workspace.router)
    app.dependency_overrides[get_current_user] = lambda: 7
    app.dependency_overrides[get_db] = lambda: object()
    monkeypatch.setattr(workspace.crud, 'get_work', lambda *args: SimpleNamespace(architecture_version=2, created_by=7, output_mode='markdown'))
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
        monkeypatch.setattr(workspace.crud, 'get_work', lambda *args: SimpleNamespace(architecture_version=2, created_by=8))
        assert client.get('/api/workspace/work/paper').status_code == 403
        assert client.post('/api/workspace/work/paper/proposals', json=request).status_code == 403


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
