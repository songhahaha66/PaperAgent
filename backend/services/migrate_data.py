"""Offline startup migration for every project, before serving requests."""
import json
from pathlib import Path

from ai_system.research.store import ResearchStore


def migrate_project(root):
    root = Path(root).resolve()
    return ResearchStore(root, 'domain-migration')


def migrate_all():
    from config.paths import get_workspaces_path
    from database.database import SessionLocal
    from models.models import Project
    with SessionLocal() as db:
        ids = [row[0] for row in db.query(Project.work_id).filter(Project.architecture_version == 2)]
    count = 0
    for project_id in ids:
        root = (get_workspaces_path() / project_id).resolve()
        if not root.is_relative_to(get_workspaces_path().resolve()):
            raise ValueError('项目工作区超出数据目录')
        if root.is_dir():
            migrate_project(root)
            count += 1
    return count


if __name__ == '__main__':
    print(json.dumps({'migrated_projects': migrate_all()}))
