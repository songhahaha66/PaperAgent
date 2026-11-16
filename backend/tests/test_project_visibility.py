"""Visibility affects list membership, never ownership or research state."""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models.models import Base, Project, User
from schemas.schemas import WorkUpdate
from services.data_services.crud import get_user_works, get_work, update_work


def test_hide_restore_preserves_project_and_filters_before_pagination():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([User(id=i, username=str(i), email=f'{i}@example.test', password_hash='test') for i in (1, 2)])
        db.add_all([
            Project(work_id='trial', title='trial', created_by=1, status='completed',
                    updated_at=datetime(2026, 11, 16, tzinfo=timezone.utc)),
            Project(work_id='normal', title='normal', created_by=1, status='failed',
                    updated_at=datetime(2026, 11, 15, tzinfo=timezone.utc)),
            Project(work_id='other', title='other', created_by=2),
        ])
        db.commit()
        with pytest.raises(HTTPException) as denied:
            update_work(db, 'trial', WorkUpdate(is_hidden=True), 2)
        assert denied.value.status_code == 403
        hidden = update_work(db, 'trial', WorkUpdate(is_hidden=True), 1)
        assert hidden.status == 'completed' and hidden.created_by == 1
        assert get_work(db, 'trial').is_hidden is True
        visible = get_user_works(db, 1, limit=1)
        assert visible['total'] == 1
        assert [p.work_id for p in visible['works']] == ['normal']
        assert get_user_works(db, 1, search='trial')['total'] == 0
        assert get_user_works(db, 1, search='trial', include_hidden=True)['total'] == 1
        assert {p.work_id for p in get_user_works(db, 1, include_hidden=True)['works']} == {'trial', 'normal'}
        update_work(db, 'trial', WorkUpdate(title='renamed'), 1)
        assert get_work(db, 'trial').is_hidden is True
        restored = update_work(db, 'trial', WorkUpdate(is_hidden=False), 1)
        assert restored.status == 'completed' and restored.title == 'renamed'
        assert get_user_works(db, 1)['total'] == 2
