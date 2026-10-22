"""Architecture cutover is explicit; old projects are export-only."""
from fastapi import HTTPException

CURRENT_ARCHITECTURE = 2


def require_current(project):
    if getattr(project, 'architecture_version', 1) != CURRENT_ARCHITECTURE:
        raise HTTPException(409, '历史项目仅支持导出。请新建项目后上传旧资料；不会恢复旧执行状态。')


def require_project(db, work_id, user_id, *, exporting=False):
    from services.data_services.crud import get_work
    project = get_work(db, work_id)
    if project is None:
        raise HTTPException(404, 'Project not found')
    if project.created_by != user_id:
        raise HTTPException(403, 'Not authorized to access this project')
    if not exporting:
        require_current(project)
    return project
