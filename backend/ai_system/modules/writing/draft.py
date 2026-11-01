"""Versioned manuscript edits by the research Agent; no second writing model."""
import hashlib
from pydantic import Field
from pydantic.json_schema import SkipJsonSchema
from ai_system.modules.registry import Inputs, CapabilityProduct


class DraftEdit(Inputs):
    path: str = Field(default='paper.md', pattern=r'\.(md|tex|bib)$')
    content: str
    expected_version: SkipJsonSchema[str | None] = None
    old_text: str | None = Field(default=None,description='局部修改时指定唯一的原文，content为替换内容；省略则写完整文件。')


def observed_version(messages,name):
    """Bind CAS to a receipt already seen by this Agent, never a model-copied hash."""
    import json
    from langchain_core.messages import ToolMessage
    for message in reversed(messages):
        if not isinstance(message,ToolMessage) or message.name not in {'research_read_artifact','writing_edit'} or message.status=='error':continue
        try:value=json.loads(message.content)
        except (ValueError,TypeError):continue
        if isinstance(value,dict) and value.get('path')==name:return value.get('version')
    return None


async def edit(ctx, inputs):
    from services.chat_services import paper_project as project
    from services.file_services.workspace_lock import edit_lock
    from ai_system.research.models import ResearchConflict
    from ai_system.research.store import digest
    name=inputs['path']
    if (ctx.research.snapshot().get('conversation_input') or {}).get('paper_context'):
        raise ValueError('本轮带有论文引用，请使用writing_propose_edits提出修改集，等待用户接受')
    if not name.endswith(('.md','.tex','.bib')):raise ValueError('正文仅支持 Markdown、TeX 或 BibTeX 文件')
    path=project.safe_path(ctx.workspace,name,writing=True)
    if name.startswith(('data/','attachment/','outputs/','code/')):raise ValueError('正文不能覆盖资料或计算产物')
    project.initialize(ctx.research)
    raw=path.read_bytes() if path.exists() else b''
    before=raw.decode('utf-8')
    version=hashlib.sha256(raw).hexdigest()
    if before and inputs['expected_version']!=version:raise ResearchConflict('正文已存在或发生变化，请先读取当前文件再修改')
    text=inputs['content'];old=inputs['old_text']
    if old is not None:
        if not old or before.count(old)!=1:raise ResearchConflict('待替换原文必须唯一，请重新读取当前稿件')
        text=before.replace(old,text,1)
    text=ctx.registry.render(text,latex=ctx.output_mode=='latex')
    if ctx.output_mode=='latex':
        from ai_system.research.citations import render_citations,literature_root
        from services.chat_services.paper_sources import write_bibliography
        write_bibliography(ctx.research.root)
        text=render_citations(literature_root(ctx),text,True)
    doc=project.snapshot(ctx.workspace)
    existing=next((f for f in doc['files'] if f['path']==name),None)
    if existing:
        if existing['text']!=before:raise ResearchConflict('正文在编辑期间改变，请读取新版本')
        patch={'file':name,'version':doc['version'],'start':0,'end':len(before),'quote':before,'replacement':text}
        change=project.propose_change(ctx.research,'research-agent','draft-edit',[patch])
        await project.decide(ctx.research,change['id'],'apply',compile_after=False,expected_version=doc['version'])
    else:
        with edit_lock(ctx.workspace):
            if path.exists() and path.read_bytes()!=raw:raise ResearchConflict('保存期间正文发生变化')
            path.parent.mkdir(parents=True,exist_ok=True)
            project.atomic_text(path,text)
        project.snapshot(ctx.workspace)
    result_version=hashlib.sha256(text.encode()).hexdigest()
    with ctx.research._transaction() as db:
        state=ctx.research._read(db)
        if state.get('conversation_input'):state['conversation_input']['manuscript_edited']=True
        main='paper.tex' if ctx.output_mode=='latex' else 'paper.md'
        if name==main:
            state['document']={'path':main,'version':result_version}
            # File text is authoritative; sections remain only an editor projection.
            state['sections']={'document':{'text':text,'revision':state.get('revision',0)+1,'content_hash':digest(text),
                'task_refs':list(state['experiments']),'evidence_refs':[],'validity':'current'}}
            state['paper_hash']=digest(text);state['paper_version']=main
        state['outcome']='running';state['delivery_stages']={};state['revision']+=1
        ctx.research._save(db,state,'manuscript_edited',{'path':name,'version':result_version})
    ctx.fs.register_file(name,kind='document',created_by='ai',visibility='user')
    await ctx.events.file_changed(name)
    return CapabilityProduct('research_state',{'path':name,'version':result_version,'revision':state['revision']})
