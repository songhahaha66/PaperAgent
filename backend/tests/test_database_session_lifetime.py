"""Long-running work must not hold a checked-out relational DB connection."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.models import Base, ChatSession, ModelConfig, Project, User
from routers.chat_routes import chat
from routers.work_routes import workspace
from services.chat_services.chat_history_manager import ChatHistoryManager
from services.chat_services.chat_service import ChatService as DurableChatService


def database(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "app.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(User(id=7, username='owner', email='owner@example.test', password_hash='unused'))
        db.add(Project(work_id='work', title='work', created_by=7, architecture_version=2))
        for role in ('brain', 'code', 'writing'):
            db.add(ModelConfig(type=role, model_id=role, base_url='https://example.test/v1',
                               api_key='offline', created_by=7))
        db.commit()
    return engine, sessions


@pytest.mark.parametrize('action', ['run', 'ask'])
@pytest.mark.parametrize('existing_session', [False, True])
def test_chat_releases_project_connection_while_agent_runs(tmp_path, monkeypatch, action, existing_session):
    engine, sessions = database(tmp_path)
    if existing_session:
        with sessions() as db:
            db.add(ChatSession(session_id='work_main_session', work_id='work',
                               system_type='brain', created_by=7))
            db.commit()
    root = tmp_path / 'work'
    root.mkdir()
    history = ChatHistoryManager(str(tmp_path))
    started, release = asyncio.Event(), asyncio.Event()

    class Service(DurableChatService):
        def __init__(self, db):
            self.db_session, self.history_manager = db, history

    class Agent:
        def __init__(self, *args, **kwargs): pass
        async def run(self, *args, **kwargs):
            started.set()
            await release.wait()

    class Handler:
        def __init__(self, *, model_config, **kwargs):
            assert model_config.model_id == 'brain'
            assert model_config.api_key == 'offline' and model_config.base_url == 'https://example.test/v1'
        def get_llm_instance(self): return object()

    monkeypatch.setattr(chat, 'get_db', lambda: iter([sessions()]))
    monkeypatch.setattr(chat, 'ChatService', Service)
    monkeypatch.setattr(chat, 'PaperGraphAgent', Agent)
    monkeypatch.setattr(chat, 'LLMHandler', Handler)
    monkeypatch.setattr(chat, 'get_workspace_path', lambda _: root)
    monkeypatch.setattr(chat, 'set_work_status', lambda *args: None)
    monkeypatch.setattr('ai_system.core_handlers.llm_providers.create_llm_from_model_config',
                        lambda model: SimpleNamespace(model_id=model.model_id))
    monkeypatch.setattr('ai_system.research.views.completed_work_status', lambda _: 'completed')
    async def send(*args, **kwargs): pass
    async def answer(*args, **kwargs):
        started.set()
        await release.wait()
    monkeypatch.setattr(chat, 'answer_research_question', answer)
    monkeypatch.setattr(chat.manager, 'send', send)
    monkeypatch.setattr(chat, 'research_snapshot', lambda *args: SimpleNamespace(model_dump=lambda: {}))
    chat.task_manager._tasks.clear()

    from services.chat_services.research_session import ResearchSnapshot
    initial_snapshot = ResearchSnapshot(work_id='work', work_status='created')
    monkeypatch.setattr(chat, 'research_snapshot', lambda *args: initial_snapshot)

    async def scenario():
        task = chat.task_manager.create_task('work', 7, 'run', action=action)
        task.request_id = 'request'
        running = asyncio.create_task(chat._execute_chat_task(
            'work', 7, 'run', action, initial_snapshot, None, None, 'created', task))
        chat.task_manager.set_async_task('work', running)
        try:
            await asyncio.wait_for(started.wait(), 5)
            assert engine.pool.checkedout() == 0, 'Agent execution retained the projects connection'
        finally:
            release.set()
            await asyncio.wait_for(running, 5)

    try:
        asyncio.run(scenario())
    finally:
        chat.task_manager._tasks.clear()
        engine.dispose()


def test_chat_initialization_failure_releases_connection(tmp_path, monkeypatch):
    engine, sessions = database(tmp_path)
    root = tmp_path / 'work'
    root.mkdir()
    monkeypatch.setattr(chat, 'get_db', lambda: iter([sessions()]))
    monkeypatch.setattr(chat, 'get_workspace_path', lambda _: root)
    monkeypatch.setattr(chat, 'set_work_status', lambda *args: None)
    def fail_environment(db, *_):
        assert db.query(Project).filter_by(work_id='work').one()
        raise ValueError('controlled initialization failure')
    monkeypatch.setattr(chat, 'setup_environment_from_db', fail_environment)
    async def send(*args, **kwargs): pass
    monkeypatch.setattr(chat.manager, 'send', send)
    chat.task_manager._tasks.clear()

    async def scenario():
        task = chat.task_manager.create_task('work', 7, 'run', action='run')
        await chat._execute_chat_task('work', 7, 'run', 'run', None, None, None, 'created', task)
        assert task.status.value == 'failed'
        assert engine.pool.checkedout() == 0

    try:
        asyncio.run(scenario())
    finally:
        chat.task_manager._tasks.clear()
        engine.dispose()


def test_compile_returns_connection_before_waiting_for_compiler(tmp_path, monkeypatch):
    engine, sessions = database(tmp_path)
    from services.data_services.crud import get_work
    from services.chat_services import paper_project
    store = SimpleNamespace(research_id='study', root=tmp_path)
    monkeypatch.setattr(workspace, '_paper_store', lambda db, work_id, user_id: (get_work(db, work_id), store)[1])

    async def compile_project(received):
        assert received is store
        assert engine.pool.checkedout() == 0, 'compiler retained the projects connection'
        await asyncio.sleep(0)
        return {'success': True}

    monkeypatch.setattr(paper_project, 'compile_project', compile_project)
    with sessions() as db:
        result = asyncio.run(workspace.paper_compile('work', {'research_id': 'study'}, db, 7))
        assert result == {'success': True}
    engine.dispose()


def test_review_start_releases_connection_after_loading_model(tmp_path, monkeypatch):
    engine, sessions = database(tmp_path)
    from services.data_services.crud import get_work
    from services.chat_services import review_tasks
    store = SimpleNamespace(research_id='study', root=tmp_path)
    monkeypatch.setattr(workspace, '_paper_store',
                        lambda db, work_id, user_id, **kwargs: (get_work(db, work_id), store)[1])
    monkeypatch.setattr(workspace, '_paper_writing_model',
                        lambda db, user_id: db.query(ModelConfig).first().model_id)
    def start(received, stage, version, command_id, *, model_factory):
        assert received is store and model_factory() == 'brain'
        return {'id': 'review', 'state': 'running'}
    monkeypatch.setattr(review_tasks, 'start', start)
    request = workspace.ReviewTaskRequest(research_id='study', stage='sections',
                                          version='a' * 64, command_id='one')
    with sessions() as db:
        result = asyncio.run(workspace.recover_review_task('work', request, db, 7))
        assert result['state'] == 'running'
        assert engine.pool.checkedout() == 0, 'review task retained the projects connection'
    engine.dispose()


def test_title_generation_releases_connection_then_updates_title(tmp_path, monkeypatch):
    engine, sessions = database(tmp_path)
    root = tmp_path / 'work'
    root.mkdir()

    class Model:
        async def ainvoke(self, messages, **kwargs):
            assert engine.pool.checkedout() == 0, 'title model retained the projects connection'
            await asyncio.sleep(0)
            return SimpleNamespace(content='研究标题')

    class Handler:
        def __init__(self, *, model_config):
            assert model_config.model_id == 'brain'
        def get_llm_instance(self): return Model()

    monkeypatch.setattr(chat, 'LLMHandler', Handler)
    monkeypatch.setattr('config.paths.get_workspace_path', lambda _: root)
    with sessions() as db:
        result = asyncio.run(chat.generate_work_title('work', {'question': '分析数据'}, 7, db))
        assert result['title'] == '研究标题' and result['status'] == 'success'
    with sessions() as db:
        assert db.query(Project).filter_by(work_id='work').one().title == '研究标题'
    engine.dispose()
