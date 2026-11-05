"""Process ownership of a whole research turn, including model waits and saving.

All backend workers for a project must share PA_DATA and flock semantics. This
lock is separate from the sandbox execution lock: a turn may recover remote jobs
while owning the coordinator. The OS releases ownership even after SIGKILL.
"""
import fcntl
from services.file_services.workspace_layout import private_root


def claim(workspace):
    path = private_root(workspace) / 'coordinator.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        return None
    except BaseException:
        stream.close()
        raise
    return stream


def recover_orphaned_work(db, work, workspace):
    """Repair only an unowned in_progress row, while excluding new admissions."""
    if getattr(work, 'status', None) != 'in_progress':
        return
    ownership = claim(workspace)
    if ownership is None:
        return
    try:
        # Compare-and-set in SQL: an initial ORM read can predate the owner's
        # final commit. Never overwrite a completed/failed row with that stale read.
        from models.models import Project
        db.query(Project).filter(Project.work_id == work.work_id,
                                 Project.status == 'in_progress').update(
            {'status': 'paused'}, synchronize_session=False)
        db.commit()
        db.refresh(work)
    finally:
        ownership.close()
