from services.file_services.workspace_layout import workspace_path
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database.database import get_db
from auth.auth import get_current_user
from services import crud
from services.file_services.workspace_files import workspace_file_service

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

def _require_workspace_access(db: Session, work_id: str, user_id: int, *, exporting: bool = False) -> None:
    from services.file_services.project_access import require_project
    return require_project(db, work_id, user_id, exporting=exporting)


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
        db.close()

        return workspace_file_service.file_response(work_id, file_path)

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
        db.close()

        return workspace_file_service.file_response(work_id, file_path, image=True)

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
        db.close()

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
        project = _require_workspace_access(db, work_id, current_user, exporting=True)
        legacy = project.architecture_version != 2
        db.close()

        # 生成ZIP文件
        zip_path = workspace_file_service.export_workspace(work_id, legacy=legacy)

        # 返回ZIP文件
        return FileResponse(
            path=zip_path,
            media_type="application/zip",
            filename=f"workspace_{work_id}.zip",
            background=__import__("starlette.background", fromlist=["BackgroundTask"]).BackgroundTask(__import__("shutil").rmtree, str(__import__("pathlib").Path(zip_path).parent))
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
from services.chat_services.paper_corrections import FragmentScope,PatchFailure
from services.chat_services.task_manager import task_manager


class SourceIssue(BaseModel):
    id:str=Field(min_length=1,max_length=64)
    version:str=Field(pattern=r'^[a-f0-9]{64}$')


class PaperEditRequest(BaseModel):
    research_id: str
    base_commit: int
    section: str
    instruction: str = Field(default='', max_length=20000)
    content: str | None = Field(default=None, max_length=100000)
    edit_kind:Literal['wording','evidence']='wording'
    section_keys:list[str]|None=Field(default=None,max_length=6)
    section_contents:dict[str,str]|None=Field(default=None,max_length=6)
    fragment_scope:FragmentScope|None=None
    recovery_of:str|None=Field(default=None,max_length=32)
    source_issue:SourceIssue|None=None


class PaperDecision(BaseModel):
    research_id: str
    decision: Literal['apply', 'reject','review']


class ReviewTaskRequest(BaseModel):
    research_id:str
    stage:Literal['reconcile','sections','citations']
    version:str=Field(min_length=64,max_length=64)
    command_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')


def _paper_writing_model(db,user_id):
    from ai_system.config.environment import setup_environment_from_db
    from ai_system.core_handlers.llm_providers import create_llm_from_model_config
    config=setup_environment_from_db(db).config_manager
    try:
        model=config.get_model_config('writing',user_id)
    except ValueError:
        try:model=config.get_model_config('brain',user_id)
        except ValueError:raise HTTPException(409,'没有配置可用模型；可直接编辑候选正文') from None
    return create_llm_from_model_config(model)


def _paper_store(db, work_id, user_id, *, mutation=False):
    _require_workspace_access(db, work_id, user_id)
    if mutation and task_manager.get_running_task(work_id):
        raise HTTPException(409, '研究正在执行，请完成后再修改论文')
    if mutation:
        from services.file_services.workspace_lock import edit_lock
        with edit_lock(workspace_file_service.get_workspace_path(work_id)):
            pass
    try:
        return paper_editor.current_store(workspace_file_service.get_workspace_path(work_id))
    except (OSError, ValueError, KeyError):
        raise HTTPException(409, '尚无可编辑的 research-v3 论文')


@router.get('/{work_id}/paper')
async def get_paper_document(work_id: str, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    _require_workspace_access(db, work_id, current_user)
    root = workspace_file_service.get_workspace_path(work_id)
    if not (workspace_path(root, '.system/graph_thread.json')).exists():
        return paper_editor.empty_document()
    store = _paper_store(db, work_id, current_user)
    db.close()
    try:
        return await paper_editor.document(store)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.get('/{work_id}/review-tasks')
async def get_review_tasks(work_id:str,db:Session=Depends(get_db),current_user:int=Depends(get_current_user)):
    store=_paper_store(db,work_id,current_user)
    if not store.snapshot():raise HTTPException(409,'研究尚未建立')
    from services.chat_services import review_tasks
    return review_tasks.status(store)


@router.get('/{work_id}/paper/issues/{issue_id}')
async def paper_issue_context(work_id:str,issue_id:str,db:Session=Depends(get_db),current_user:int=Depends(get_current_user)):
    store=_paper_store(db,work_id,current_user)
    from services.chat_services.issue_edits import context
    try:return context(store,issue_id)
    except ValueError as exc:raise HTTPException(404,str(exc))


@router.post('/{work_id}/review-tasks',status_code=202)
async def recover_review_task(work_id:str,request:ReviewTaskRequest,db:Session=Depends(get_db),current_user:int=Depends(get_current_user)):
    store=_paper_store(db,work_id,current_user,mutation=True)
    if request.research_id!=store.research_id:raise HTTPException(409,'研究标识已变化，请刷新')
    from services.chat_services import review_tasks
    try:
        result=review_tasks.start(store,request.stage,request.version,request.command_id,model_factory=lambda:_paper_writing_model(db,current_user))
        db.close()
        return result
    except (ValueError,ResearchConflict) as exc:raise HTTPException(409,str(exc))


@router.post('/{work_id}/paper/proposals')
async def propose_paper_edit(work_id: str, request: PaperEditRequest, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    store = _paper_store(db, work_id, current_user, mutation=True)
    if crud.get_work(db, work_id).output_mode == 'latex':
        raise HTTPException(409, 'LaTeX 请使用源码评论与文件修改集')
    if request.research_id != store.research_id or request.base_commit != store.snapshot()['commit_seq']:
        raise HTTPException(409, '研究版本已改变，请刷新论文')
    llm = None
    if request.source_issue is not None or request.content is None or '{{C:' in request.content:
        llm = _paper_writing_model(db,current_user)
    db.close()
    try:
        from ai_system.research.runtime import budget_scope, ResearchBudgetExhausted
        from ai_system.graph.tracing import LLMCallTrace
        trace=LLMCallTrace()
        if llm is not None and hasattr(llm,'model_copy'):
            callbacks=getattr(llm,'callbacks',None)
            llm=llm.model_copy(update={'callbacks':[*(callbacks if isinstance(callbacks,list) else []),trace]})
        with budget_scope(store.root,store.research_id):
            try:
                if request.edit_kind=='wording' and request.section_keys is None and request.fragment_scope is None and request.source_issue is None:
                    return await paper_editor.propose(store, request.section, request.instruction, request.content, llm)
                options={'edit_kind':request.edit_kind,'section_keys':request.section_keys}
                if request.source_issue is not None:options['source_issue']=request.source_issue.model_dump()
                if request.section_contents is not None:options['section_contents']=request.section_contents
                if request.fragment_scope is not None:options['fragment_scope']=request.fragment_scope.model_dump()
                if request.recovery_of is not None:options['recovery_of']=request.recovery_of
                return await paper_editor.propose(store,request.section,request.instruction,request.content,llm,**options)
            finally:trace.dump(workspace_path(store.root, '.system/editor_llm_calls.json'))
    except PatchFailure as exc:
        raise HTTPException(409,exc.detail) from None
    except (ValueError, ResearchConflict, ResearchBudgetExhausted) as exc:
        raise HTTPException(409, str(exc))
    except RuntimeError as exc:
        detail=('模型回复达到输出长度上限，原论文已保留。可缩小范围或直接编辑候选正文。'
                if getattr(exc.__cause__,'truncated',False) else '候选生成未完成，原论文已保留。请核对请求余量并缩小修改范围。')
        raise HTTPException(409,detail) from None


@router.post('/{work_id}/paper/proposals/{proposal_id}')
async def decide_paper_edit(work_id: str, proposal_id: str, request: PaperDecision, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    store = _paper_store(db, work_id, current_user, mutation=True)
    output_mode = crud.get_work(db, work_id).output_mode or 'markdown'
    if output_mode == 'latex':
        raise HTTPException(409, 'LaTeX 请使用源码评论与文件修改集')
    if request.research_id != store.research_id:
        raise HTTPException(409, '研究标识已改变，请刷新论文')
    try:
        if request.decision=='review':
            from ai_system.research.runtime import budget_scope
            from ai_system.graph.tracing import LLMCallTrace
            from services.chat_services.paper_corrections import review_pending_fragment
            llm=_paper_writing_model(db,current_user);trace=LLMCallTrace()
            db.close()
            if hasattr(llm,'model_copy'):
                callbacks=getattr(llm,'callbacks',None)
                llm=llm.model_copy(update={'callbacks':[*(callbacks if isinstance(callbacks,list) else []),trace]})
            try:
                with budget_scope(store.root,store.research_id):return await review_pending_fragment(store,proposal_id,llm)
            finally:trace.dump(workspace_path(store.root, '.system/editor_llm_calls.json'))
        db.close()
        return await paper_editor.decide(store, proposal_id, request.decision == 'apply', output_mode)
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
    thread = workspace_path(root, '.system/graph_thread.json')
    info = json.loads(thread.read_text()) if thread.exists() else {}
    research_id = info.get('thread_id') if info.get('engine_version') == 'research-v3' else None
    return ResearchStore(root, research_id or 'workspace-skills')


@router.get('/{work_id}/skills')
async def get_research_skills(work_id: str, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    from ai_system.modules.methods import skill_catalog
    store = _skill_store(db, work_id, current_user)
    snapshot = store.snapshot()
    from ai_system.research.domain import experiment_design
    usage = [{'task_id': key, 'skills': experiment_design(snapshot,task)['skills'], 'validity': task['validity']}
             for key, task in (snapshot or {}).get('experiments', {}).items() if experiment_design(snapshot,task) and not task['retired']]
    return {**store.skill_preferences(), 'catalog': skill_catalog(), 'usage': usage,
            'has_research': bool(snapshot)}


@router.get('/{work_id}/skills/{skill_key}')
async def get_research_skill_detail(work_id: str, skill_key: str, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    from ai_system.modules.methods import skill_detail
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


class LiteratureQuery(BaseModel):
    query: str = Field(min_length=2,max_length=300)
    limit: int = Field(default=3,ge=1,le=5)
    fetch_open_access: bool = True


class LiteratureURL(BaseModel):
    url: str = Field(min_length=8,max_length=2048)


class LiteratureClaim(BaseModel):
    claim: str = Field(min_length=10,max_length=1500)


class LiteraturePolicy(BaseModel):
    mode: str


def _literature(work_id, db, user_id, mutation=False):
    _require_workspace_access(db,work_id,user_id)
    if mutation and task_manager.get_running_task(work_id):
        raise HTTPException(409,'研究正在执行，请完成后再修改文献资料')
    from ai_system.research.literature import LiteratureService
    return LiteratureService(workspace_file_service.get_workspace_path(work_id))


@router.get('/{work_id}/literature')
async def literature_state(work_id: str, db: Session=Depends(get_db), current_user: int=Depends(get_current_user)):
    service = _literature(work_id,db,current_user)
    from ai_system.research.citations import citation_usage
    suggestion = ''
    thread = workspace_path(service.store.root, '.system/graph_thread.json')
    try:
        if thread.exists():
            import json
            from ai_system.research.store import ResearchStore
            saved = json.loads(thread.read_text())
            snapshot = ResearchStore(service.store.root,saved['thread_id'],read_only=True).snapshot()
            suggestion = (snapshot or {}).get('spec',{}).get('requirement','')[:300]
        return {**service.store.state(),'citations':citation_usage(service.store.root),'query_suggestion':suggestion}
    except (ValueError,OSError,KeyError):
        return {**service.store.state(),'citations':[],'query_suggestion':suggestion,'citations_error':'研究引用记录暂不可读，文献资料仍保留'}


@router.put('/{work_id}/literature/policy')
async def literature_policy(work_id: str, request: LiteraturePolicy, db: Session=Depends(get_db), current_user: int=Depends(get_current_user)):
    service = _literature(work_id,db,current_user,True)
    try:
        service.store.set_policy(request.mode)
        return {'mode':service.store.policy()}
    except ValueError as exc:
        raise HTTPException(409,str(exc))


@router.post('/{work_id}/literature/search')
async def literature_search(work_id: str, request: LiteratureQuery, db: Session=Depends(get_db), current_user: int=Depends(get_current_user)):
    service = _literature(work_id,db,current_user,True)
    db.close()
    try:
        from ai_system.modules.registry import invoke
        from types import SimpleNamespace
        return await invoke(SimpleNamespace(workspace=service.store.root), 'literature.collect', query=request.query,limit=request.limit,fetch_open_access=request.fetch_open_access)
    except ValueError as exc:
        raise HTTPException(409,str(exc))


@router.post('/{work_id}/literature/import')
async def literature_import(work_id: str, request: LiteratureURL, db: Session=Depends(get_db), current_user: int=Depends(get_current_user)):
    service = _literature(work_id,db,current_user,True)
    db.close()
    try:
        from ai_system.modules.registry import invoke
        from types import SimpleNamespace
        return await invoke(SimpleNamespace(workspace=service.store.root), 'literature.import', url=request.url)
    except ValueError as exc:
        raise HTTPException(409,str(exc))


@router.get('/{work_id}/literature/sources/{source_id}')
async def literature_source(work_id: str, source_id: str, db: Session=Depends(get_db), current_user: int=Depends(get_current_user)):
    service = _literature(work_id,db,current_user)
    try:
        return {**service.store.source(source_id),'evidence':service.store.passages(source_id)}
    except ValueError as exc:
        raise HTTPException(404,str(exc))


@router.post('/{work_id}/literature/sources/{source_id}/fetch')
async def literature_fetch(work_id: str, source_id: str, db: Session=Depends(get_db), current_user: int=Depends(get_current_user)):
    service = _literature(work_id,db,current_user,True)
    db.close()
    try:
        return await service.fetch_source(source_id)
    except ValueError as exc:
        raise HTTPException(409,str(exc))


@router.post('/{work_id}/literature/retrieve')
async def literature_retrieve(work_id: str, request: LiteratureQuery, db: Session=Depends(get_db), current_user: int=Depends(get_current_user)):
    service = _literature(work_id,db,current_user)
    return {'evidence':service.store.retrieve(request.query,request.limit)}


@router.get('/{work_id}/literature/evidence/{evidence_id}')
async def literature_evidence(work_id: str, evidence_id: str, db: Session=Depends(get_db), current_user: int=Depends(get_current_user)):
    service = _literature(work_id,db,current_user)
    try:
        return service.store.evidence(evidence_id)
    except ValueError as exc:
        raise HTTPException(404,str(exc))


@router.post('/{work_id}/literature/evidence/{evidence_id}/check')
async def literature_check(work_id: str, evidence_id: str, request: LiteratureClaim, db: Session=Depends(get_db), current_user: int=Depends(get_current_user)):
    service = _literature(work_id,db,current_user,True)
    from ai_system.research.citations import audit_citations, claims_in
    from ai_system.config.environment import setup_environment_from_db
    from ai_system.core_handlers.llm_providers import create_llm_from_model_config
    try:
        service.store.evidence(evidence_id)
        config = setup_environment_from_db(db).config_manager
        model = config.get_model_config('writing',current_user)
        llm = create_llm_from_model_config(model)
        db.close()
        text = request.claim+' {{C:'+evidence_id+'}}'
        from ai_system.research.runtime import budget_scope
        with budget_scope(service.store.root):
            checks = await audit_citations(service.store.root,[text],llm)
        row = claims_in(text,service.store)[0]
        return service.store.review(row['key']) or (checks[0] if checks else {'status':'insufficient','reason':'核查未完成'})
    except ValueError as exc:
        raise HTTPException(409,str(exc))


def _experience_scope(work_id,db,user_id):
    _require_workspace_access(db,work_id,user_id)
    from services.research_experience import ExperienceScope
    return ExperienceScope(user_id,work_id)


@router.get('/{work_id}/experience-usage')
async def experience_list(work_id:str,db:Session=Depends(get_db),current_user:int=Depends(get_current_user)):
    scope=_experience_scope(work_id,db,current_user)
    from services.research_experience import current_usage
    from ai_system.research.store import ResearchStore
    import json
    result={};root=workspace_file_service.get_workspace_path(work_id)
    thread=workspace_path(root, '.system/graph_thread.json')
    try:
        saved=json.loads(thread.read_text()) if thread.exists() else None
        store=ResearchStore(root,saved['thread_id'],read_only=True) if saved else None
        result['usage']=current_usage(store) if store else []
    except (OSError,ValueError,KeyError):
        result['usage']=[];result['usage_error']='当前研究经验记录暂不可读'
    for usage in result['usage']:
        usage['available']=scope.valid_refs(usage['refs'])
        usage['sources']=scope.reference_sources(usage['refs'])
    return result


@router.get('/{work_id}/experiments')
async def comparison_list(work_id:str,db:Session=Depends(get_db),current_user:int=Depends(get_current_user)):
    _require_workspace_access(db,work_id,current_user)
    # Compatibility reader only: viewing history never creates a store or policy.
    from ai_system.research.store import ResearchStore,digest
    from ai_system.research.validation import protocol
    store=ResearchStore(workspace_file_service.get_workspace_path(work_id),'workspace-comparisons',read_only=True)
    policy={'enabled':False,'spec':None,'version':0};records=[]
    if store.path.is_file():
        with store._connect() as conn:
            tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'experiment_policy' in tables:
                row=conn.execute('SELECT version,payload FROM experiment_policy WHERE id=1').fetchone()
                if row:policy={**json.loads(row['payload']),'version':row['version'],'enabled':False}
            if 'experiment_comparisons' in tables:
                records=[json.loads(row[0]) for row in conn.execute('SELECT payload FROM experiment_comparisons ORDER BY rowid DESC LIMIT 30')]
        files=store.file_inputs()
        for record in records:
            source=ResearchStore(store.root,record['research_id'],read_only=True).snapshot()
            task=(source or {}).get('experiments',{}).get(record['task_id'])
            record['historical']=True
            record['input_current']=files==record['file_inputs'] and (record.get('policy_version') is None or policy['version']==record['policy_version']) and bool(task and task['revision']==record['revision'] and digest(protocol(source,record['task_id']))==record['contract_hash'] and (not record.get('intent_hash') or digest(task.get('experiment'))==record['intent_hash']))
    return {'policy':policy,'files':[],'comparisons':records}


class ResearchSetupRequest(BaseModel):
    model_config = {"extra": "forbid"}
    request_limit:int|None=Field(default=None,ge=1)
    timeout_seconds:float|None=Field(default=None,gt=0)
    output_token_limit:int|None=Field(default=None,ge=1)
    total_token_limit:int|None=Field(default=None,ge=1)
    cost_limit_usd:float|None=Field(default=None,gt=0,allow_inf_nan=False)
    compact_paper:bool=True
    reuse_experience:bool=True
    literature_mode:Literal['off','on_request','auto']='on_request'
    literature_query:str=Field(default='',max_length=300)
    source_urls:list[str]=Field(default_factory=list,max_length=3)
    skill_keys:list[str]=Field(default_factory=list,max_length=25)


@router.put('/{work_id}/research-setup')
async def research_setup(work_id:str,request:ResearchSetupRequest,db:Session=Depends(get_db),current_user:int=Depends(get_current_user)):
    _require_workspace_access(db,work_id,current_user)
    if task_manager.get_running_task(work_id):raise HTTPException(409,'研究执行中不能修改入口配置')
    db.close()
    import json
    from ai_system.research.store import ResearchStore
    from ai_system.research.runtime import ResearchRuntime
    from ai_system.research.literature import LiteratureService
    from ai_system.modules.methods import validate_skill_policy
    from services.research_experience import ExperienceScope
    from services.literature_fetch import public_url
    root=workspace_file_service.get_workspace_path(work_id)
    thread=workspace_path(root, '.system/graph_thread.json')
    if thread.exists():raise HTTPException(409,'研究已经开始；入口配置不会覆盖检查点，请使用对应面板明确修订')
    try:
        urls=[public_url(u) for u in request.source_urls]
        skill=validate_skill_policy({'mode':'selected' if request.skill_keys else 'auto','selected':request.skill_keys,'disabled':[]})
        runtime=ResearchRuntime(root)
        runtime.configure({k:getattr(request,k) for k in ('request_limit','timeout_seconds','compact_paper','output_token_limit','total_token_limit','cost_limit_usd')})
        skills=ResearchStore(root,'workspace-skills');skills.set_skill_preferences(skill,skills.skill_preferences()['version'])
        ExperienceScope(current_user,work_id).set_enabled(request.reuse_experience)
        library=LiteratureService(root);library.store.set_policy(request.literature_mode)
        for url in urls:
            known=next((s for s in library.store.state()['sources'] if s['url']==url and s.get('evidence_count')),None)
            if not known:
                from ai_system.modules.registry import invoke
                from types import SimpleNamespace
                await invoke(SimpleNamespace(workspace=root),'literature.import',url=url)
        if request.literature_query.strip() and not any(o.get('query')==request.literature_query for o in library.store.state()['operations']):
            from ai_system.modules.registry import invoke
            from types import SimpleNamespace
            await invoke(SimpleNamespace(workspace=root),'literature.collect',query=request.literature_query,limit=1,fetch_open_access=False)
        return {'configured':True,'literature':library.store.state()}
    except ValueError as exc:raise HTTPException(422,str(exc))


# Unified contextual comments and file-backed draft editing. The research loop
# still owns formal experiments and acceptance; drafts remain accessible.
from services.chat_services.paper_references import PaperSelection


class FilePatch(PaperSelection):
    replacement: str = Field(max_length=100000)


class PaperFileEdit(BaseModel):
    research_id: str
    patches: list[FilePatch] = Field(min_length=1,max_length=24)
    thread_id: str | None = None


class PaperChangeDecision(BaseModel):
    research_id: str
    decision: Literal['apply','reject','revert']


def _paper_identity(store, research_id):
    if store.research_id != research_id:
        raise HTTPException(409,'研究标识已改变，请刷新')


@router.get('/{work_id}/paper/project')
async def paper_project_state(work_id: str, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    store=_paper_store(db,work_id,current_user)
    from services.chat_services import paper_project,paper_sources
    try:
        project=paper_project.describe(store)
        main=next((f for f in project['files'] if f['path']==project['main']), {})
        output_mode = crud.get_work(db,work_id).output_mode
        db.close()
        draft = await paper_editor.document(store) if output_mode != 'latex' else None
        return {**project,'research_id':store.research_id,'sources':paper_sources.cards(store),
                **({'draft_sections':draft['sections']} if draft else {}),
                'has_source':bool((main.get('text') or '').strip())}
    except (ValueError,ResearchConflict,OSError) as exc:
        raise HTTPException(409,str(exc))


@router.post('/{work_id}/paper/locate')
async def paper_locate(work_id: str, request: dict, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    store=_paper_store(db,work_id,current_user)
    from services.chat_services.paper_project import candidates
    quote=str(request.get('quote',''))
    if not 1 <= len(quote) <= 100000:
        raise HTTPException(422,'请选择有效阅读文字')
    try:return {'candidates':candidates(store.root,quote),'confirmation_required':True}
    except (ValueError,ResearchConflict,OSError) as exc:raise HTTPException(409,str(exc))


@router.post('/{work_id}/paper/changes')
async def paper_file_change(work_id: str, request: PaperFileEdit, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    store=_paper_store(db,work_id,current_user)
    _paper_identity(store,request.research_id)
    from services.chat_services import paper_project
    try:
        paper_project.initialize(store)
        return paper_project.propose_change(store,request.thread_id,None,[p.model_dump() for p in request.patches])
    except (ValueError,ResearchConflict,OSError) as exc:raise HTTPException(409,str(exc))


@router.post('/{work_id}/paper/changes/{change_id}')
async def paper_change_decision(work_id: str, change_id: str, request: PaperChangeDecision, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    store=_paper_store(db,work_id,current_user)
    _paper_identity(store,request.research_id)
    from services.chat_services import paper_project
    db.close()
    try:return await paper_project.decide(store,change_id,request.decision)
    except (ValueError,ResearchConflict,OSError) as exc:raise HTTPException(409,str(exc))


@router.post('/{work_id}/paper/compile')
async def paper_compile(work_id: str, request: dict, db: Session = Depends(get_db), current_user: int = Depends(get_current_user)):
    store=_paper_store(db,work_id,current_user)
    _paper_identity(store,request.get('research_id'))
    from services.chat_services import paper_project
    db.close()
    try:return await paper_project.compile_project(store)
    except (ValueError,ResearchConflict,OSError) as exc:raise HTTPException(409,str(exc))
