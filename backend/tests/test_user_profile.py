import base64
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.auth import create_access_token
from database.database import get_db
from models.models import Base, User
from routers.auth_routes.auth import router


@pytest.fixture
def profile_client():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add_all([User(id=1, username='first', email='first@example.com', password_hash='unused'),
                    User(id=2, username='second', email='second@example.com', password_hash='unused')])
        db.commit()

    def database():
        with sessions() as db:
            yield db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer ' + create_access_token({'user_id': 1})
        yield client
    engine.dispose()


def image_data():
    output = BytesIO()
    Image.new('RGB', (600, 300), 'red').save(output, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()


def test_profile_persists_normalized_avatar_and_name(profile_client):
    response = profile_client.patch('/auth/me', json={'username': '  新用户名  ', 'avatar_url': image_data()})
    assert response.status_code == 200, response.text
    user = response.json()
    assert user['id'] == 1 and user['email'] == 'first@example.com'
    assert user['username'] == '新用户名'
    with Image.open(BytesIO(base64.b64decode(user['avatar_url'].split(',')[1]))) as image:
        assert image.format == 'WEBP' and image.size == (256, 256)
    assert profile_client.get('/auth/me').json() == user
    # Updating a name does not remove the avatar, and keeping one's name is legal.
    assert profile_client.patch('/auth/me', json={'username': '新用户名'}).json()['avatar_url'] == user['avatar_url']
    assert profile_client.patch('/auth/me', json={'avatar_url': None}).json()['avatar_url'] is None


def test_duplicate_name_does_not_partially_change_avatar(profile_client):
    response = profile_client.patch('/auth/me', json={'username': 'second', 'avatar_url': image_data()})
    assert response.status_code == 409
    assert profile_client.get('/auth/me').json()['avatar_url'] is None
    assert profile_client.get('/auth/me').json()['username'] == 'first'


@pytest.mark.parametrize('changes', [
    {'username': ''}, {'username': ' '}, {'username': 'a'}, {'username': 'x' * 51},
    {'username': None}, {'username': 'ab\ncd'}, {'id': 2}, {'email': 'changed@example.com'},
    {'avatar_url': 'https://example.com/avatar.png'},
    {'avatar_url': 'data:image/svg+xml;base64,PHN2Zz4='},
    {'avatar_url': 'data:image/png;base64,not-an-image'},
    {'avatar_url': 'data:image/png;base64,' + base64.b64encode(b'x' * (2 * 1024 * 1024 + 1)).decode()},
])
def test_invalid_profiles_are_rejected(profile_client, changes):
    assert profile_client.patch('/auth/me', json=changes).status_code == 422
    assert profile_client.get('/auth/me').json()['username'] == 'first'


def test_profile_requires_auth_and_only_updates_owner(profile_client):
    profile_client.patch('/auth/me', json={'username': 'renamed'})
    profile_client.headers['Authorization'] = 'Bearer ' + create_access_token({'user_id': 2})
    assert profile_client.get('/auth/me').json()['username'] == 'second'
    profile_client.headers.pop('Authorization')
    assert profile_client.patch('/auth/me', json={'username': 'anonymous'}).status_code == 401


def test_avatar_migration_preserves_existing_user(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    url = f'sqlite:///{tmp_path / "migration.db"}'
    monkeypatch.setenv('DATABASE_URL', url)
    config = Config('alembic.ini')
    command.upgrade(config, 'd31a70f92b46')
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO users (username,email,password_hash) VALUES ('existing','existing@example.com','preserved')"))
    command.upgrade(config, 'head')
    with engine.connect() as db:
        assert db.execute(text('SELECT username,password_hash,avatar_url FROM users')).one() == ('existing', 'preserved', None)
    command.downgrade(config, 'd31a70f92b46')
    with engine.connect() as db:
        assert db.execute(text('SELECT username,password_hash FROM users')).one() == ('existing', 'preserved')
    engine.dispose()
