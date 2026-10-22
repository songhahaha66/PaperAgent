"""Exercise real legacy databases and the single registered execution boundary."""
import json
import sqlite3

import pytest

from ai_system.research.store import ResearchStore
from services.migrate_data import migrate_project


def legacy(root, *, conflicting=False):
    store = ResearchStore(root, 'study')
    state = store.initialize('原始研究目标', {'subproblems':[{'id':'Q1','title':'目标','question':'原始研究目标','depends_on':[]}],
        'requirements':[{'id':'RQ1','statement':'原始研究目标','source_quote':'原始研究目标','kind':'goal','task_ids':['Q1']}], 'assumptions':[]})
    state['schema_version'] = 2
    # Construct the actual v2 storage shape, rather than relabel a current DTO.
    state['tasks'] = state.pop('experiments')
    state['attempts'] = state.pop('executions')
    state['evidence'] = state.pop('artifacts')
    state.pop('questions')
    for task in state['tasks'].values():
        task['active_attempt'] = task.pop('execution_id')
        task.pop('question_id')
        task.pop('report_ref')
    state.pop('feedback');state.pop('faults');state.pop('legacy_records')
    from ai_system.research.models import Issue
    state['issues'] = [Issue(id='science',task_id='Q1',task_revision=1,category='model',message='原文意见',source='original',status='confirmed',action='verify').model_dump(),
                       Issue(id='outage',task_id='Q1',task_revision=1,category='service',message='超时原文',source='original',status='confirmed',action='retry').model_dump(),
                       {'id':'unknown','category':'unclassified','message':'不可推断的原文'}]
    path = root / '.system/research.sqlite'
    path.unlink()
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE research(id TEXT PRIMARY KEY,payload TEXT NOT NULL);'
                         'CREATE TABLE comparisons(id TEXT PRIMARY KEY,payload TEXT);'
                         'CREATE TABLE comparison_policy(id INTEGER PRIMARY KEY,version INTEGER,payload TEXT);')
        db.execute('INSERT INTO research VALUES (?,?)', ('study', json.dumps(state, ensure_ascii=False)))
        db.execute('INSERT INTO comparisons VALUES (?,?)', ('comparison', '{"original":"unchanged"}'))
        if conflicting:
            db.execute('CREATE TABLE experiment_comparisons(id TEXT)')
    return path, state


def test_transactional_migration_removes_old_sources_and_preserves_unmapped_history(tmp_path):
    path, old = legacy(tmp_path)
    raw = b'{"ambiguous": ["original report", 123]}'
    (path.parent / 'results.json').write_bytes(raw)
    migrated = migrate_project(tmp_path)
    state = ResearchStore(tmp_path, 'study', read_only=True).snapshot()
    assert state['schema_version'] == 5 and 'issues' not in state
    assert state['feedback'] == [old['issues'][0]]
    assert state['faults'] == [old['issues'][1]]
    assert state['legacy_records'] == [{'source':'research/study/issues','raw':old['issues'][2]}]
    with migrated._connect() as db:
        assert db.execute('SELECT payload FROM experiment_comparisons').fetchone()[0] == '{"original":"unchanged"}'
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='comparisons'").fetchone()
        assert db.execute('SELECT raw FROM legacy_records WHERE source=?', ('.system/results.json',)).fetchone()[0] == raw
    # Repeating startup never creates another row or changes payloads.
    migrate_project(tmp_path)
    assert ResearchStore(tmp_path, 'study').snapshot() == state


def test_conflicting_sources_roll_back_entire_workspace_migration(tmp_path):
    path, old = legacy(tmp_path, conflicting=True)
    with pytest.raises(ValueError, match='两套'):
        ResearchStore(tmp_path, 'study')
    with sqlite3.connect(path) as db:
        assert json.loads(db.execute('SELECT payload FROM research').fetchone()[0]) == old
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='domain_migrations'").fetchone()


def test_application_migration_moves_real_rows_without_promoting_experience(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    import sqlalchemy as sa
    backend = Path(__file__).resolve().parents[1]
    url = 'sqlite:///' + str(tmp_path / 'migration.sqlite')
    monkeypatch.setenv('DATABASE_URL', url)
    config = Config(str(backend / 'alembic.ini'))
    config.set_main_option('script_location', str(backend / 'alembic'))
    command.upgrade(config, 'f1b90a3d725e')
    engine = sa.create_engine(url)
    metadata = sa.MetaData()
    users = sa.Table('users', metadata, autoload_with=engine)
    works = sa.Table('works', metadata, autoload_with=engine)
    experiences = sa.Table('research_experiences', metadata, autoload_with=engine)
    with engine.begin() as db:
        db.execute(users.insert().values(id=1,username='owner',email='owner@example.test',password_hash='unused',is_active=True))
        db.execute(works.insert().values(work_id='original',title='原研究',description='原始需求',status='paused',progress=10,created_by=1,output_mode='markdown'))
        db.execute(experiences.insert().values(id='Eold',user_id=1,source_work_id='original',source_key='source',version=7,content_hash='a'*64,payload={'original':'原文'},enabled=True,deleted=False))
    command.upgrade(config, 'head')
    with engine.connect() as db:
        assert 'works' not in sa.inspect(db).get_table_names()
        assert 'work_flow_states' not in sa.inspect(db).get_table_names()
        row = db.execute(sa.text('SELECT work_id,title,description,created_by FROM projects')).one()
        assert tuple(row) == ('original','原研究','原始需求',1)
        row = db.execute(sa.text('SELECT status,version,payload,history FROM research_experiences')).one()
        assert row.status == 'candidate' and row.version == 7
        assert json.loads(row.payload) == {'original':'原文'}
        assert json.loads(row.history)[0]['version'] == 7
        assert 'progress' not in {c['name'] for c in sa.inspect(db).get_columns('projects')}
        archived = db.execute(sa.text("SELECT payload FROM legacy_domain_records WHERE source='works/original/progress'")).scalar_one()
        assert json.loads(archived) == {'progress': 10}
    engine.dispose()
