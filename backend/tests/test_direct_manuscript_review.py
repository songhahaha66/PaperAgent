"""Review real manuscript/run records without creating an experiment. No HTTP."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from helpers.review import no_network
from ai_system.graph.state import ClaimAudit, ClaimIssue
from ai_system.modules.writing import consistency
from ai_system.research.models import IssueReport
from ai_system.research.review_cache import ReviewCache
from ai_system.research.store import ResearchStore
from services.chat_services import issue_edits, review_tasks
from services.file_services.workspace_fs import WorkspaceFS
from services.file_services.workspace_layout import workspace_path


@pytest.fixture
def direct(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path, 'direct-review')
    store.initialize('计算实际结果并说明适用范围。')
    workspace_path(tmp_path, '.system/graph_thread.json').write_text(json.dumps({'thread_id':store.research_id}))
    store.save_sections({'document': '本次结果证明所有未来数据均有效。'}, [])
    fs = WorkspaceFS(str(tmp_path))
    run = fs.create_run()
    fs._manifest['runs'][run].update(research_id=store.research_id, scope='research')
    fs.save_run_input(run, 'print("observed_score=2")')
    fs.save_run_stdout(run, 'observed_score=2\n')
    fs.finish_run(run)
    model = SimpleNamespace(model_name='offline-direct', calls=0, error=None)

    async def controlled(llm, schema, messages, validate=None, **kwargs):
        llm.calls += 1
        if llm.error:
            raise llm.error
        assert schema is ClaimAudit
        assert 'observed_score=2' in messages[-1].content
        section = 'paper.tex' if store.snapshot()['spec']['output_mode'] == 'latex' else 'document'
        result = ClaimAudit(issues=[ClaimIssue(section=section, problem='未来数据结论超出实际计算范围',
                                              quote='本次结果证明所有未来数据均有效。')])
        if validate:
            validate(result)
        return result

    monkeypatch.setattr(consistency, 'call_structured', controlled)
    return SimpleNamespace(store=store, fs=fs, run=run, model=model)


def task(store, stage='sections'):
    return next(t for t in review_tasks.status(store)['tasks'] if t['stage'] == stage)


async def review(store, model, command, stage='sections'):
    item = task(store, stage)
    assert item['can_resume'], item
    review_tasks.start(store, stage, item['version'], command, model)
    await asyncio.wait_for(asyncio.gather(*list(review_tasks.ACTIVE)), 3)


@pytest.mark.parametrize('native', [False, True])
def test_document_findings_are_saved_and_locatable_without_experiments(direct, native):
    v = direct
    if native:
        with v.store._transaction() as db:
            state = v.store._read(db)
            state['spec']['output_mode'] = 'latex'
            v.store._save(db, state, 'fixture_native', {})
        (v.store.root / 'paper.tex').write_text(
            '\\documentclass{article}\n\\begin{document}\n本次结果证明所有未来数据均有效。\n\\end{document}')
    asyncio.run(review(v.store, v.model, 'review-document'))
    assert task(v.store)['state'] == 'completed'
    assert task(v.store)['outcome'] == 'findings'
    state = v.store.snapshot()
    assert state['experiments'] == {} and state['executions'] == {}
    finding = next(i for i in state['feedback'] if i['category'] == 'writing')
    assert finding['task_id'] is None and not finding['blocking']
    from ai_system.research.planner import decision_context
    assert finding['id'] in {issue['id'] for issue in decision_context(v.store, state)[1]['issues']}
    location = issue_edits.context(v.store, finding['id'])
    assert location['origin_current'] and location['section'] == ('paper.tex' if native else 'document')
    # Re-reading the status must not rerun the model or turn its own finding into a new input.
    assert task(v.store)['state'] == 'completed' and v.model.calls == 1


def test_service_failure_and_retry_do_not_require_a_dummy_experiment(direct):
    v = direct
    v.model.error = TimeoutError('controlled offline failure')
    asyncio.run(review(v.store, v.model, 'first-review'))
    assert task(v.store)['state'] == 'failed'
    assert all(i['task_id'] is None for i in v.store.snapshot()['faults'])
    v.model.error = None
    asyncio.run(review(v.store, v.model, 'retry-review'))
    assert task(v.store)['state'] == 'completed'
    assert not any(i['status'] not in {'resolved', 'superseded'} for i in v.store.snapshot()['faults'])
    assert v.store.snapshot()['experiments'] == {} and v.model.calls == 2


def test_citation_findings_have_document_scope_without_experiments(direct):
    from helpers.literature import Judge, seed
    v = direct
    _, _, passage = seed(v.store.root)
    v.store.save_sections({'document': '文献证明本研究的未来预测一定正确 {{C:' + passage['id'] + '}}'}, [])
    asyncio.run(review(v.store, Judge(status='unsupported'), 'review-citations', 'citations'))
    assert task(v.store, 'citations')['state'] == 'completed', task(v.store, 'citations')
    finding = next(i for i in v.store.snapshot()['feedback'] if i['category'] == 'writing')
    assert finding['task_id'] is None and finding['section'] == 'document'
    assert v.store.snapshot()['experiments'] == {}


def test_changed_direct_output_invalidates_a_completed_review(direct):
    v = direct
    asyncio.run(review(v.store, v.model, 'first-version'))
    previous = task(v.store)
    v.fs.save_run_stdout(v.run, 'observed_score=2\nadditional result: scope is limited\n')
    current = task(v.store)
    assert current['version'] != previous['version'] and current['can_resume']
    assert current['state'] != 'completed'


@pytest.mark.parametrize('source', ['segmented-aggregate', 'issue-long-composition'])
def test_retired_records_remain_history_but_cannot_complete_current_review(direct, source):
    store = direct.store
    cache = ReviewCache(store)
    context = {'section_coverage': {'complete': True, 'sections': {}}}
    record = cache.start('sections', context)
    record = cache.complete(record, ClaimAudit(issues=[]).model_dump())
    record['source'] = source
    with store._transaction() as db:
        db.execute('UPDATE review_records SET payload=? WHERE id=?', (json.dumps(record), record['id']))
    assert cache.get('sections', context, ClaimAudit) is None
    assert not review_tasks.valid_result({**record, 'state': 'completed'}, store)
    with pytest.raises(ValueError, match='历史审查'):
        cache.reuse(record)
    with pytest.raises(ValueError, match='已停用'):
        cache.start('sections', context, source=source)
    assert any(row['id'] == record['id'] for row in review_tasks.status(store)['history'])


def test_unknown_experiment_is_rejected_instead_of_silently_becoming_document_scope(direct):
    with pytest.raises(ValueError, match='未知任务'):
        direct.store.report_issues([IssueReport(task_id='missing', category='writing', message='invalid owner')], 'review-task:sections')
