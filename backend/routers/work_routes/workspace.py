from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database.database import get_db
from auth.auth import get_current_user
from services import crud
from services.file_services.workspace_files import workspace_file_service
from urllib.parse import unquote

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

def _require_workspace_access(db: Session, work_id: str, user_id: int, *, exporting: bool = False) -> None:
    work = crud.get_work(db, work_id)
    if not work:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found")
    if work.created_by != user_id:
        action = "export" if exporting else "access"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Not authorized to {action} this workspace")


@router.get("/{work_id}/files")
async def list_workspace_files(
    work_id: str,
    path: str = Query("", description="相对路径，默认为根目录"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """列出工作空间中的文件"""
    try:
        _require_workspace_access(db, work_id, current_user)
        
        return workspace_file_service.list_files(work_id, path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{work_id}/download/{file_path:path}")
async def download_workspace_file(
    work_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """下载工作空间中的文件"""
    try:
        _require_workspace_access(db, work_id, current_user)

        # 构建文件路径
        workspace_path = workspace_file_service.get_workspace_path(work_id)
        target_file = workspace_path / file_path

        if not target_file.exists() or not target_file.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )

        # 获取MIME类型
        import mimetypes
        mime_type, _ = mimetypes.guess_type(str(target_file))
        if mime_type is None:
            mime_type = "application/octet-stream"

        # 返回文件下载响应
        return FileResponse(
            path=str(target_file),
            media_type=mime_type,
            filename=target_file.name
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{work_id}/files/{file_path:path}")
async def read_workspace_file(
    work_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """读取工作空间中的文件内容"""
    try:
        _require_workspace_access(db, work_id, current_user)

        return workspace_file_service.read_file(work_id, file_path)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{work_id}/images/{file_path:path}")
async def get_workspace_image(
    work_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """获取工作空间中的图片文件"""
    try:
        _require_workspace_access(db, work_id, current_user)
        
        # 获取图片文件的完整路径
        workspace_path = workspace_file_service.get_workspace_path(work_id)
        image_path = workspace_path / unquote(file_path)
        
        if not image_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Image file not found"
            )
        
        if not image_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path is not a file"
            )
        
        # 检查文件大小
        if image_path.stat().st_size > 50 * 1024 * 1024:  # 50MB限制
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Image file too large"
            )
        
        # 返回文件响应
        return FileResponse(
            path=str(image_path),
            media_type=f"image/{file_path.split('.')[-1].lower()}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/{work_id}/files/{file_path:path}")
async def write_workspace_file(
    work_id: str,
    file_path: str,
    content: str = Form(..., description="文件内容"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """写入文件到工作空间"""
    try:
        _require_workspace_access(db, work_id, current_user)
        
        return workspace_file_service.write_file(work_id, file_path, content)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/{work_id}/upload")
async def upload_file_to_workspace(
    work_id: str,
    file_path: str = Form(..., description="目标文件路径"),
    file: UploadFile = Form(..., description="上传的文件"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """上传文件到工作空间"""
    try:
        _require_workspace_access(db, work_id, current_user)
        
        return workspace_file_service.upload_file(work_id, file_path, file)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.delete("/{work_id}/files/{file_path:path}")
async def delete_workspace_file(
    work_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """删除工作空间中的文件或目录"""
    try:
        _require_workspace_access(db, work_id, current_user)
        
        return workspace_file_service.delete_file(work_id, file_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/{work_id}/mkdir")
async def create_workspace_directory(
    work_id: str,
    dir_path: str = Form(..., description="目录路径"),
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """在工作空间中创建目录"""
    try:
        _require_workspace_access(db, work_id, current_user)
        
        return workspace_file_service.create_directory(work_id, dir_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )




@router.get("/{work_id}/export")
async def export_workspace(
    work_id: str,
    db: Session = Depends(get_db),
    current_user: int = Depends(get_current_user)
):
    """导出工作空间为ZIP文件"""
    try:
        _require_workspace_access(db, work_id, current_user, exporting=True)

        # 生成ZIP文件
        zip_path = workspace_file_service.export_workspace(work_id)

        # 返回ZIP文件
        return FileResponse(
            path=zip_path,
            media_type="application/zip",
            filename=f"workspace_{work_id}.zip",
            headers={"Content-Disposition": f"attachment; filename=workspace_{work_id}.zip"}
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )
# Section candidates are separate from file writes: no publication before review.
from pydantic import BaseModel, Field
from typing import Literal
from ai_system.research.models import ResearchConflict
from services.chat_services import paper_editor
from services.chat_services.task_manager import task_manager


class PaperEditRequest(BaseModel):
    research_id: str
    base_commit: int
    section: str
    instruction: str = Field(default='', max_length=20000)
    content: str | None = Field(default=None, max_length=100000)


class PaperDecision(BaseModel):
    research_id: str
    decision: Literal['apply', 'reject']


def _paper_store(db, work_id, user_id, *, mutation=False):
    _require_workspace_access(db, work_id, user_id)
    if mutation and task_manager.get_running_task(work_id):
        raise HTTPException(409, '研究正在执行，请完成后再修改论文')
    try:
        return paper_editor.current_store(workspace_file_service.get_workspace_path(work_id))
    except (OSError, ValueError, KeyError):
        raise HTTPException(409, '尚无可编辑的 research-v3 论文')


@router.get('/{work_id}/paper')
async def get_paper_document(work_id: str, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    store = _paper_store(db, work_id, current_user)
    try:
        return await paper_editor.document(store)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.post('/{work_id}/paper/proposals')
async def propose_paper_edit(work_id: str, request: PaperEditRequest, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    store = _paper_store(db, work_id, current_user, mutation=True)
    if request.research_id != store.research_id or request.base_commit != store.snapshot()['commit_seq']:
        raise HTTPException(409, '研究版本已改变，请刷新论文')
    llm = None
    if request.content is None:
        from ai_system.config.environment import setup_environment_from_db
        from ai_system.core_handlers.llm_providers import create_llm_from_model_config
        config = setup_environment_from_db(db).config_manager
        model = config.get_model_config('writing', current_user) or config.get_model_config('brain', current_user)
        if model is None:
            raise HTTPException(409, '没有配置写作模型；可直接编辑候选正文')
        llm = create_llm_from_model_config(model)
    try:
        return await paper_editor.propose(store, request.section, request.instruction, request.content, llm)
    except (ValueError, ResearchConflict) as exc:
        raise HTTPException(409, str(exc))


@router.post('/{work_id}/paper/proposals/{proposal_id}')
async def decide_paper_edit(work_id: str, proposal_id: str, request: PaperDecision, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    store = _paper_store(db, work_id, current_user, mutation=True)
    if request.research_id != store.research_id:
        raise HTTPException(409, '研究标识已改变，请刷新论文')
    try:
        return await paper_editor.decide(store, proposal_id, request.decision == 'apply', crud.get_work(db, work_id).output_mode or 'markdown')
    except (ValueError, ResearchConflict) as exc:
        raise HTTPException(409, str(exc))


class SkillSelection(BaseModel):
    base_version: int = Field(ge=0)
    policy: dict


def _skill_store(db, work_id, user_id, *, mutation=False):
    _require_workspace_access(db, work_id, user_id)
    if mutation and task_manager.get_running_task(work_id):
        raise HTTPException(409, '研究正在执行，请暂停后修改技能选择')
    import json
    from ai_system.research.store import ResearchStore
    root = workspace_file_service.get_workspace_path(work_id)
    thread = root / '.system/graph_thread.json'
    info = json.loads(thread.read_text()) if thread.exists() else {}
    research_id = info.get('thread_id') if info.get('engine_version') == 'research-v3' else None
    return ResearchStore(root, research_id or 'workspace-skills')


@router.get('/{work_id}/skills')
async def get_research_skills(work_id: str, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    from ai_system.graph.methods import skill_catalog
    store = _skill_store(db, work_id, current_user)
    snapshot = store.snapshot()
    usage = [{'task_id': key, 'skills': task.get('design', {}).get('skills', []), 'validity': task['validity']}
             for key, task in (snapshot or {}).get('tasks', {}).items() if task.get('design') and not task['retired']]
    return {**store.skill_preferences(), 'catalog': skill_catalog(), 'usage': usage,
            'has_research': bool(snapshot)}


@router.get('/{work_id}/skills/{skill_key}')
async def get_research_skill_detail(work_id: str, skill_key: str, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    from ai_system.graph.methods import skill_detail
    _require_workspace_access(db, work_id, current_user)
    try:
        return skill_detail(skill_key)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.put('/{work_id}/skills')
async def save_research_skills(work_id: str, request: SkillSelection, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    store = _skill_store(db, work_id, current_user, mutation=True)
    try:
        return store.set_skill_preferences(request.policy, request.base_version)
    except (ValueError, ResearchConflict) as exc:
        raise HTTPException(409, str(exc))
